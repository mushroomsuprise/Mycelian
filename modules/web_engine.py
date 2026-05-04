#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import copy
import dataclasses  # Added for converting PSNData to dict
import glob
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import eventlet
import eventlet.green.select
import eventlet.green.socket
import eventlet.green.subprocess
import eventlet.green.threading
import eventlet.green.time
import eventlet.wsgi
from engineio.async_drivers import gevent
from engineio.payload import Payload
from flask import Flask, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

from . import alertutils, database_manager, statistics_manager, twitch
from .dataobjects import state_manager  # To access live PSN data
from .theme_manager import generate_css_variables, get_theme_manager
from .path_utils import (
    get_assets_path,
    get_data_path,
    get_static_path,
    get_template_path,
)
from .psnapi import PSNData  # For type hinting if needed, and default object
from .template_config_parser import (
    TemplateConfigParser,
    resolve_dynamic_control_values_from_elements,
)

logger = logging.getLogger(__name__)

# Default preview profile when template JSON has no ``preview_behavior`` root key.
_PREVIEW_ANIMATED_DEMO_TEMPLATES = frozenset(
    {"alerts", "pausedalerts", "chat", "activity_feed"}
)


def resolve_template_preview_profile(
    template_name: str, template_config: Dict[str, Any]
) -> str:
    """
    Return ``persistent`` (static overlay) or ``animated_demo`` (mock activity in preview).

    Optional root key ``preview_behavior`` in the template JSON may be
    ``persistent`` or ``animated_demo``.
    """
    if not isinstance(template_config, dict):
        return "persistent"
    raw = template_config.get("preview_behavior")
    if raw in ("persistent", "animated_demo"):
        return str(raw)
    if template_name in _PREVIEW_ANIMATED_DEMO_TEMPLATES:
        return "animated_demo"
    return "persistent"


ALERT_PLAYING = False
ALERTS_PAUSED = False

# Global flag to track Web Engine status
web_engine_running = False

# Global instance for access by other modules
web_engine_instance = None


class WebEngine:
    def __init__(self, template_dir="templates", host="127.0.0.1", port=5000):
        """
        Initialize the WebEngine server

        Args:
            template_dir (str): Directory containing HTML templates
            host (str): Host IP to run the server on
            port (int): Port number to run the server on
        """
        logger.debug(f"Initializing WebEngine with host={host}, port={port}")
        # Use path utils to get correct template directory for exe
        self.template_dir = (
            get_template_path() if template_dir == "templates" else template_dir
        )
        self.host = host
        self.port = port

        # Keep track of registered template routes for dynamic management
        self._registered_template_routes = set()
        self._route_counter = 0  # Counter for unique endpoint names

        # Thread lock for authentication synchronization
        self._auth_lock = threading.Lock()

        # Custom Sources iframe preview: token -> {template, overrides, ts}
        self._preview_sessions_lock = threading.Lock()
        self._preview_sessions: Dict[str, Dict[str, Any]] = {}

        # Log template directory info
        logger.debug(f"Template directory: {template_dir}")
        logger.debug(f"Template directory exists: {os.path.exists(template_dir)}")
        if os.path.exists(template_dir):
            logger.debug(
                f"Template directory absolute path: {os.path.abspath(template_dir)}"
            )

        # Initialize Flask app and SocketIO with template reloading enabled
        self.app = Flask(
            __name__, template_folder=self.template_dir
        )  # Don't set static_folder to avoid conflicts

        # Enable Flask's built-in template reloading and development features
        self.app.config["TEMPLATES_AUTO_RELOAD"] = True
        self.app.config["SEND_FILE_MAX_AGE_DEFAULT"] = (
            0  # Disable caching for development
        )
        self.app.jinja_env.auto_reload = True

        # Configure SocketIO with increased limits to prevent "Too many packets in payload" errors
        # This can happen during startup when there are rapid bursts of Socket.IO events
        self.socketio = SocketIO(
            self.app,
            cors_allowed_origins="*",
            async_mode="gevent",
            # Configure Engine.IO server to handle more packets and larger payloads
            # These settings are passed through to the underlying Engine.IO server
            max_http_buffer_size=10000000,  # 10MB instead of default 1MB
            ping_timeout=60,
            ping_interval=25,
            # Additional Engine.IO configuration to handle packet bursts
            logger=False,  # Disable engineio debug logging
            engineio_logger=False,
        )

        # Add CORS headers to all responses for Stream Deck plugin compatibility
        @self.app.after_request
        def add_cors_headers(response):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization"
            )
            logger.debug("CORS headers added to response")
            return response

        # Handle preflight OPTIONS requests
        @self.app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
        @self.app.route("/<path:path>", methods=["OPTIONS"])
        def handle_options(path):
            return (
                "",
                200,
                {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                },
            )

        # Add custom static file route to handle subdirectories properly
        @self.app.route("/assets/<path:filename>")
        def serve_static_file(filename):
            logger.debug(f"Custom route: Attempting to serve static file: {filename}")
            assets_path = get_assets_path()
            full_path = os.path.join(assets_path, filename)

            logger.debug(f"Assets folder path: {assets_path}")
            logger.debug(f"Requested file full path: {full_path}")
            logger.debug(f"File exists: {os.path.exists(full_path)}")
            logger.debug(f"Working directory: {os.getcwd()}")

            try:
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    logger.debug(f"Successfully serving static file: {filename}")
                    # Use send_from_directory with the parent directory and full relative path
                    directory_path = os.path.dirname(full_path)
                    filename_only = os.path.basename(full_path)
                    logger.debug(
                        f"Sending from directory: {directory_path}, filename: {filename_only}"
                    )
                    return send_from_directory(directory_path, filename_only)
                else:
                    logger.warning(f"Static file not found: {full_path}")
                    return f"File not found: {filename}", 404
            except Exception as e:
                logger.error(
                    f"Error serving static file {filename}: {str(e)}", exc_info=True
                )
                return f"Error serving file: {filename}", 500

        # Also add the standard /static/ route with the same functionality for backward compatibility
        @self.app.route("/static/<path:filename>")
        def serve_static_file_standard(filename):
            logger.debug(
                f"Standard static route: Attempting to serve static file: {filename}"
            )
            assets_path = get_assets_path()
            full_path = os.path.join(assets_path, filename)

            logger.debug(f"Assets folder path: {assets_path}")
            logger.debug(f"Requested file full path: {full_path}")
            logger.debug(f"File exists: {os.path.exists(full_path)}")
            logger.debug(f"Working directory: {os.getcwd()}")

            try:
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    logger.debug(f"Successfully serving static file: {filename}")
                    # Use send_from_directory with the parent directory and full relative path
                    directory_path = os.path.dirname(full_path)
                    filename_only = os.path.basename(full_path)
                    logger.debug(
                        f"Sending from directory: {directory_path}, filename: {filename_only}"
                    )
                    return send_from_directory(directory_path, filename_only)
                else:
                    logger.warning(f"Static file not found: {full_path}")
                    return f"File not found: {filename}", 404
            except Exception as e:
                logger.error(
                    f"Error serving static file {filename}: {str(e)}", exc_info=True
                )
                return f"Error serving file: {filename}", 500

        # Add debug route to list available static files
        @self.app.route("/debug/static")
        def debug_static():
            assets_path = get_assets_path()
            files_list = []

            if os.path.exists(assets_path):
                for root, dirs, files in os.walk(assets_path):
                    for file in files:
                        rel_path = os.path.relpath(
                            os.path.join(root, file), assets_path
                        )
                        files_list.append(rel_path)

            from .path_utils import get_working_directory

            return {
                "assets_path": assets_path,
                "working_directory": get_working_directory(),
                "files": sorted(files_list),
                "assets_exists": os.path.exists(assets_path),
            }

        # Add route to serve template configurations with dynamic controls
        @self.app.route("/api/template-configs")
        def serve_template_configs():
            """Serve template configurations with dynamic controls as JSON"""
            try:
                configs = {}
                config_parser = self.template_config_parser

                # Get all config files
                config_files = config_parser.get_config_files()

                for config_name in config_files:
                    try:
                        # Load config with dynamic controls
                        config = config_parser.load_config(
                            config_name, include_dynamic_controls=True
                        )

                        # Only include configs that have dynamic_controls
                        if isinstance(config, dict) and "dynamic_controls" in config:
                            dynamic_controls = resolve_dynamic_control_values_from_elements(
                                config
                            )
                            if (
                                isinstance(dynamic_controls, dict)
                                and "elements" in dynamic_controls
                            ):
                                if dynamic_controls[
                                    "elements"
                                ]:  # Only include if there are elements
                                    configs[config_name] = dynamic_controls
                                    logger.debug(
                                        f"Added dynamic controls for {config_name}: {len(dynamic_controls['elements'])} elements"
                                    )
                    except Exception as e:
                        logger.warning(
                            f"Error loading dynamic controls for {config_name}: {str(e)}"
                        )
                        continue

                logger.debug(
                    f"Serving template configs for {len(configs)} templates with dynamic controls"
                )
                return configs, 200, {"Content-Type": "application/json"}

            except Exception as e:
                logger.error(f"Error serving template configs: {str(e)}", exc_info=True)
                return (
                    {"error": "Failed to load template configurations"},
                    500,
                    {"Content-Type": "application/json"},
                )

        # Add route to serve ALL template configurations (raw JSON files)
        @self.app.route("/api/all-template-configs")
        def serve_all_template_configs():
            """Serve ALL template configurations as raw JSON"""
            try:
                configs = {}
                config_parser = self.template_config_parser

                # Get all config files
                config_files = config_parser.get_config_files()
                logger.debug(
                    f"Loading all template configs for {len(config_files)} templates"
                )

                for config_name in config_files:
                    try:
                        # Load raw config without any filtering
                        config = config_parser.load_config(
                            config_name, include_dynamic_controls=False
                        )
                        configs[config_name] = config
                        logger.debug(f"Added config for {config_name}")
                    except Exception as e:
                        logger.warning(
                            f"Error loading config for {config_name}: {str(e)}"
                        )
                        continue

                logger.debug(
                    f"Serving all template configs for {len(configs)} templates"
                )
                return configs, 200, {"Content-Type": "application/json"}

            except Exception as e:
                logger.error(
                    f"Error serving all template configs: {str(e)}", exc_info=True
                )
                return (
                    {"error": "Failed to load template configurations"},
                    500,
                    {"Content-Type": "application/json"},
                )

        # Add Stream Deck API endpoints
        @self.app.route("/api/streamdeck/toggle_alerts", methods=["POST"])
        def streamdeck_toggle_alerts():
            """Stream Deck endpoint to toggle alert pause/resume status"""
            try:
                logger.info("Stream Deck: Toggle alerts requested")

                # Toggle the pause status
                global ALERTS_PAUSED
                old_status = ALERTS_PAUSED
                ALERTS_PAUSED = not ALERTS_PAUSED

                logger.info(
                    f"Stream Deck: Alerts paused status changed from {old_status} to {ALERTS_PAUSED}"
                )

                # Emit appropriate events to all connected templates
                if ALERTS_PAUSED:
                    self.socketio.emit("alerts_paused", {"paused": True})
                else:
                    self.socketio.emit("alerts_resumed", {"paused": False})

                # Also emit the general status update
                self.socketio.emit("pause_status_update", {"paused": ALERTS_PAUSED})

                return (
                    {
                        "success": True,
                        "paused": ALERTS_PAUSED,
                        "message": f"Alerts {'paused' if ALERTS_PAUSED else 'resumed'}",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error toggling alerts: {str(e)}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "message": "Failed to toggle alerts",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        # Add deep-linking endpoint for Stream Deck communication
        @self.app.route("/api/streamdeck/deeplink/<action>", methods=["GET", "POST"])
        def streamdeck_deeplink(action):
            """Deep-linking endpoint for Stream Deck plugin communication"""
            try:
                global ALERTS_PAUSED
                global web_engine_running

                logger.info(f"Stream Deck: Deep link action requested - {action}")

                if action == "toggle_alerts":
                    # Toggle the pause status
                    old_status = ALERTS_PAUSED
                    ALERTS_PAUSED = not ALERTS_PAUSED

                    logger.info(
                        f"Stream Deck: Alerts paused status changed from {old_status} to {ALERTS_PAUSED}"
                    )

                    # Emit appropriate events to all connected templates
                    if ALERTS_PAUSED:
                        self.socketio.emit("alerts_paused", {"paused": True})
                    else:
                        self.socketio.emit("alerts_resumed", {"paused": False})

                    # Also emit the general status update
                    self.socketio.emit("pause_status_update", {"paused": ALERTS_PAUSED})

                    return (
                        {
                            "success": True,
                            "action": "toggle_alerts",
                            "paused": ALERTS_PAUSED,
                            "message": f"Alerts {'paused' if ALERTS_PAUSED else 'resumed'}",
                        },
                        200,
                        {"Content-Type": "application/json"},
                    )

                elif action == "template_action":
                    # Handle template action execution
                    try:
                        data = (
                            request.get_json()
                            if request.is_json
                            else request.args.to_dict()
                        )

                        template_name = data.get("templateName", "")
                        action_name = data.get("actionName", "")
                        event_name = data.get(
                            "eventName", action_name
                        )  # Use action_name as fallback
                        action_data = data.get("actionData", {})

                        if not template_name or not action_name:
                            return (
                                {
                                    "success": False,
                                    "error": "Missing required fields",
                                    "message": "templateName and actionName are required",
                                },
                                400,
                                {"Content-Type": "application/json"},
                            )

                        logger.info(
                            f"Stream Deck: Template action requested - {template_name}.{action_name} (event: {event_name})"
                        )

                        # Emit the template action event to all connected clients
                        event_data = {
                            "templateName": template_name,
                            "actionName": action_name,
                            "eventName": event_name,
                            "actionData": action_data,
                        }

                        self.socketio.emit("streamdeck_template_action", event_data)

                        # Also emit specific template events for backward compatibility
                        self.socketio.emit(
                            f"{template_name}_{action_name}", action_data
                        )

                        # Emit event-specific events if event_name is different from action_name
                        if event_name != action_name:
                            self.socketio.emit(
                                f"{template_name}_{event_name}", action_data
                            )

                        logger.debug(
                            f"Stream Deck: Emitted template action events for {template_name}.{action_name}"
                        )

                        return (
                            {
                                "success": True,
                                "templateName": template_name,
                                "actionName": action_name,
                                "eventName": event_name,
                                "message": f"Executed {template_name}.{action_name} (event: {event_name})",
                            },
                            200,
                            {"Content-Type": "application/json"},
                        )

                    except Exception as e:
                        logger.error(
                            f"Stream Deck: Error executing template action: {str(e)}",
                            exc_info=True,
                        )
                        return (
                            {
                                "success": False,
                                "error": str(e),
                                "message": "Failed to execute template action",
                            },
                            500,
                            {"Content-Type": "application/json"},
                        )

                elif action == "get_template_actions":
                    # Return available template actions
                    try:
                        logger.debug("Stream Deck: Get template actions requested")

                        actions_list = []
                        template_configs = (
                            self.template_config_parser.get_config_files()
                        )

                        for template_name in template_configs:
                            try:
                                # Load template configuration with Stream Deck options
                                config = self.template_config_parser.load_config(
                                    template_name, include_streamdeck_options=True
                                )

                                if (
                                    isinstance(config, dict)
                                    and "streamdeck_options" in config
                                ):
                                    streamdeck_options = config["streamdeck_options"]

                                    if "actions" in streamdeck_options:
                                        template_actions = streamdeck_options["actions"]

                                        for (
                                            action_key,
                                            action_config,
                                        ) in template_actions.items():
                                            if isinstance(action_config, dict):
                                                action_info = {
                                                    "template_name": template_name,
                                                    "action_key": action_key,
                                                    "action_name": action_config.get(
                                                        "name", action_key
                                                    ),
                                                    "description": action_config.get(
                                                        "description", ""
                                                    ),
                                                    "event": action_config.get(
                                                        "event",
                                                        f"{template_name}_{action_key}",
                                                    ),
                                                    "default_data": action_config.get(
                                                        "default_data", {}
                                                    ),
                                                    "template_description": streamdeck_options.get(
                                                        "description", ""
                                                    ),
                                                    "category": "streamdeck_action",
                                                }

                                                actions_list.append(action_info)

                                # Also check for dynamic controls that can be used as actions
                                if (
                                    isinstance(config, dict)
                                    and "dynamic_controls" in config
                                ):
                                    dynamic_controls = config["dynamic_controls"]

                                    if "elements" in dynamic_controls:
                                        for element in dynamic_controls["elements"]:
                                            if (
                                                isinstance(element, dict)
                                                and "action" in element
                                            ):
                                                element_type = element.get("type", "")
                                                action_name = element.get("action", "")

                                                # Create action info for dynamic controls
                                                action_info = {
                                                    "template_name": template_name,
                                                    "action_key": f"{template_name}_{action_name}",
                                                    "action_name": element.get(
                                                        "label", action_name
                                                    ),
                                                    "description": element.get(
                                                        "description", ""
                                                    ),
                                                    "event": f"{template_name}_{action_name}",
                                                    "default_data": {},
                                                    "element_type": element_type,
                                                    "category": "dynamic_control",
                                                }

                                                # Add type-specific data based on element configuration
                                                if element_type == "counter_control":
                                                    if action_name in [
                                                        "increment",
                                                        "counter_increment",
                                                    ] or action_name.endswith(
                                                        "_increment"
                                                    ):
                                                        action_info["default_data"] = {
                                                            "action": "increment"
                                                        }
                                                    elif action_name in [
                                                        "decrement",
                                                        "counter_decrement",
                                                    ] or action_name.endswith(
                                                        "_decrement"
                                                    ):
                                                        action_info["default_data"] = {
                                                            "action": "decrement"
                                                        }
                                                    elif action_name in [
                                                        "reset",
                                                        "counter_reset",
                                                    ] or action_name.endswith("_reset"):
                                                        action_info["default_data"] = {
                                                            "action": "reset"
                                                        }
                                                elif element_type in [
                                                    "number_input",
                                                    "text_input",
                                                ]:
                                                    if "value" in element:
                                                        if (
                                                            element_type
                                                            == "number_input"
                                                        ):
                                                            action_info[
                                                                "default_data"
                                                            ] = {
                                                                "value": element.get(
                                                                    "value", 0
                                                                )
                                                            }
                                                        else:
                                                            action_info[
                                                                "default_data"
                                                            ] = {
                                                                "text": element.get(
                                                                    "value", ""
                                                                )
                                                            }
                                                elif element_type == "button":
                                                    action_info["default_data"] = {}

                                                actions_list.append(action_info)

                            except Exception as e:
                                logger.warning(
                                    f"Error processing template {template_name}: {str(e)}"
                                )
                                continue

                        logger.debug(
                            f"Stream Deck: Found {len(actions_list)} template actions across {len(template_configs)} templates"
                        )

                        return (
                            {
                                "success": True,
                                "actions": actions_list,
                                "count": len(actions_list),
                                "templates_count": len(template_configs),
                                "message": f"Found {len(actions_list)} actions from {len(template_configs)} templates",
                            },
                            200,
                            {"Content-Type": "application/json"},
                        )

                    except Exception as e:
                        logger.error(
                            f"Stream Deck: Error getting template actions: {str(e)}",
                            exc_info=True,
                        )
                        return (
                            {
                                "success": False,
                                "error": str(e),
                                "actions": [],
                                "count": 0,
                                "message": "Failed to load template actions",
                            },
                            500,
                            {"Content-Type": "application/json"},
                        )

                elif action == "check_connection":
                    # Check connection status
                    try:
                        logger.debug("Stream Deck: Connection check requested")

                        return (
                            {
                                "success": True,
                                "connected": True,
                                "server_running": web_engine_running,
                                "alerts_paused": ALERTS_PAUSED,
                                "timestamp": datetime.now().isoformat(),
                                "message": "Mycelian server is running and responsive",
                            },
                            200,
                            {"Content-Type": "application/json"},
                        )

                    except Exception as e:
                        logger.error(
                            f"Stream Deck: Error in connection check: {str(e)}",
                            exc_info=True,
                        )
                        return (
                            {
                                "success": False,
                                "connected": False,
                                "error": str(e),
                                "message": "Server error occurred",
                            },
                            500,
                            {"Content-Type": "application/json"},
                        )

                else:
                    return (
                        {
                            "success": False,
                            "error": "Unknown action",
                            "message": f"Unsupported deep link action: {action}",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error in deep link handler: {str(e)}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "message": "Failed to handle deep link request",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/streamdeck/get_pause_status", methods=["GET"])
        def streamdeck_get_pause_status():
            """Stream Deck endpoint to get current alert pause status"""
            try:
                logger.debug("Stream Deck: Get pause status requested")

                global ALERTS_PAUSED

                return (
                    {
                        "success": True,
                        "paused": ALERTS_PAUSED,
                        "message": f"Alerts are currently {'paused' if ALERTS_PAUSED else 'active'}",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error getting pause status: {str(e)}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "message": "Failed to get pause status",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/streamdeck/template_action", methods=["POST"])
        def streamdeck_template_action():
            """Stream Deck endpoint to execute template actions"""
            try:
                data = request.get_json()
                if not data:
                    return (
                        {
                            "success": False,
                            "error": "No JSON data provided",
                            "message": "Request must contain JSON data",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )

                template_name = data.get("templateName", "")
                action_name = data.get("actionName", "")
                event_name = data.get(
                    "eventName", action_name
                )  # Use action_name as fallback
                action_data = data.get("actionData", {})

                if not template_name or not action_name:
                    return (
                        {
                            "success": False,
                            "error": "Missing required fields",
                            "message": "templateName and actionName are required",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )

                logger.info(
                    f"Stream Deck: Template action requested - {template_name}.{action_name} (event: {event_name})"
                )

                # Emit the template action event to all connected clients
                event_data = {
                    "templateName": template_name,
                    "actionName": action_name,
                    "eventName": event_name,
                    "actionData": action_data,
                }

                self.socketio.emit("streamdeck_template_action", event_data)

                # Also emit specific template events for backward compatibility
                self.socketio.emit(f"{template_name}_{action_name}", action_data)

                # Emit event-specific events if event_name is different from action_name
                if event_name != action_name:
                    self.socketio.emit(f"{template_name}_{event_name}", action_data)

                logger.debug(
                    f"Stream Deck: Emitted template action events for {template_name}.{action_name}"
                )

                return (
                    {
                        "success": True,
                        "templateName": template_name,
                        "actionName": action_name,
                        "eventName": event_name,
                        "message": f"Executed {template_name}.{action_name} (event: {event_name})",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error executing template action: {str(e)}",
                    exc_info=True,
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "message": "Failed to execute template action",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/streamdeck/get_template_actions", methods=["GET"])
        def streamdeck_get_template_actions():
            """Stream Deck endpoint to get available template actions from template configurations"""
            try:
                logger.debug("Stream Deck: Get template actions requested")

                actions_list = []
                template_configs = self.template_config_parser.get_config_files()

                for template_name in template_configs:
                    try:
                        # Load template configuration with Stream Deck options
                        config = self.template_config_parser.load_config(
                            template_name, include_streamdeck_options=True
                        )

                        if isinstance(config, dict) and "streamdeck_options" in config:
                            streamdeck_options = config["streamdeck_options"]

                            if "actions" in streamdeck_options:
                                template_actions = streamdeck_options["actions"]

                                for (
                                    action_key,
                                    action_config,
                                ) in template_actions.items():
                                    if isinstance(action_config, dict):
                                        action_info = {
                                            "template_name": template_name,
                                            "action_key": action_key,
                                            "action_name": action_config.get(
                                                "name", action_key
                                            ),
                                            "description": action_config.get(
                                                "description", ""
                                            ),
                                            "event": action_config.get(
                                                "event", f"{template_name}_{action_key}"
                                            ),
                                            "default_data": action_config.get(
                                                "default_data", {}
                                            ),
                                            "template_description": streamdeck_options.get(
                                                "description", ""
                                            ),
                                            "category": "streamdeck_action",
                                        }

                                        actions_list.append(action_info)

                        # Also check for dynamic controls that can be used as actions
                        if isinstance(config, dict) and "dynamic_controls" in config:
                            dynamic_controls = config["dynamic_controls"]

                            if "elements" in dynamic_controls:
                                for element in dynamic_controls["elements"]:
                                    if (
                                        isinstance(element, dict)
                                        and "action" in element
                                    ):
                                        element_type = element.get("type", "")
                                        action_name = element.get("action", "")

                                        # Create action info for dynamic controls
                                        action_info = {
                                            "template_name": template_name,
                                            "action_key": f"{template_name}_{action_name}",
                                            "action_name": element.get(
                                                "label", action_name
                                            ),
                                            "description": element.get(
                                                "description", ""
                                            ),
                                            "event": f"{template_name}_{action_name}",
                                            "default_data": {},
                                            "element_type": element_type,
                                            "category": "dynamic_control",
                                        }

                                        # Add type-specific data based on element configuration
                                        if element_type == "counter_control":
                                            if action_name in [
                                                "increment",
                                                "counter_increment",
                                            ] or action_name.endswith("_increment"):
                                                action_info["default_data"] = {
                                                    "action": "increment"
                                                }
                                            elif action_name in [
                                                "decrement",
                                                "counter_decrement",
                                            ] or action_name.endswith("_decrement"):
                                                action_info["default_data"] = {
                                                    "action": "decrement"
                                                }
                                            elif action_name in [
                                                "reset",
                                                "counter_reset",
                                            ] or action_name.endswith("_reset"):
                                                action_info["default_data"] = {
                                                    "action": "reset"
                                                }
                                        elif element_type in [
                                            "number_input",
                                            "text_input",
                                        ]:
                                            if "value" in element:
                                                if element_type == "number_input":
                                                    action_info["default_data"] = {
                                                        "value": element.get("value", 0)
                                                    }
                                                else:
                                                    action_info["default_data"] = {
                                                        "text": element.get("value", "")
                                                    }
                                        elif element_type == "button":
                                            action_info["default_data"] = {}

                                        actions_list.append(action_info)

                    except Exception as e:
                        logger.warning(
                            f"Error processing template {template_name}: {str(e)}"
                        )
                        continue

                logger.debug(
                    f"Stream Deck: Found {len(actions_list)} template actions across {len(template_configs)} templates"
                )

                return (
                    {
                        "success": True,
                        "actions": actions_list,
                        "count": len(actions_list),
                        "templates_count": len(template_configs),
                        "message": f"Found {len(actions_list)} actions from {len(template_configs)} templates",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error getting template actions: {str(e)}",
                    exc_info=True,
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "actions": [],
                        "count": 0,
                        "message": "Failed to load template actions",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/streamdeck/check_connection", methods=["GET"])
        def streamdeck_check_connection():
            """Stream Deck endpoint to check server connection and status"""
            try:
                logger.debug("Stream Deck: Connection check requested")

                global ALERTS_PAUSED
                global web_engine_running

                return (
                    {
                        "success": True,
                        "connected": True,
                        "server_running": web_engine_running,
                        "alerts_paused": ALERTS_PAUSED,
                        "timestamp": datetime.now().isoformat(),
                        "message": "Mycelian server is running and responsive",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error in connection check: {str(e)}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "connected": False,
                        "error": str(e),
                        "message": "Server error occurred",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        # WebSocket-based Stream Deck communication handlers
        self.register_streamdeck_websocket_handlers()

        # Initialize template config parser
        self.template_config_parser = TemplateConfigParser()

        # Register dynamic routes based on HTML templates
        self.register_routes()

        # WebSocket event handlers
        self.register_socket_events()

        # Thread for running the server
        self.server_thread = None
        self.is_running = False

    def register_routes(self):
        """Register dynamic routes based on HTML templates in the template directory"""
        logger.debug("Registering template routes...")

        # Get current HTML files in template directory
        current_templates = set()
        if os.path.exists(self.template_dir):
            html_files = glob.glob(os.path.join(self.template_dir, "*.html"))
            current_templates = {
                os.path.basename(f).replace(".html", "") for f in html_files
            }
            logger.debug(
                f"Found {len(current_templates)} HTML templates: {sorted(current_templates)}"
            )
        else:
            logger.warning(f"Template directory {self.template_dir} not found.")
            return

        # Find templates to add and remove
        templates_to_add = current_templates - self._registered_template_routes
        templates_to_remove = self._registered_template_routes - current_templates

        if templates_to_remove:
            logger.info(f"Templates removed: {sorted(templates_to_remove)}")
            # Note: Flask doesn't support dynamic route removal,
            # but we track this for logging and potential future use

        if templates_to_add:
            logger.info(f"Adding new templates: {sorted(templates_to_add)}")

        # Register routes for new templates
        for template_name in templates_to_add:
            self._register_template_route(template_name)

        # Update our tracking set
        self._registered_template_routes = current_templates

        logger.debug(
            f"Template route registration complete. Total routes: {len(self._registered_template_routes)}"
        )

    def _register_template_route(self, template_name):
        """Register a single template route"""
        try:
            route_path = f"/{template_name}"
            # Create unique endpoint name using counter to avoid conflicts
            self._route_counter += 1
            endpoint_name = f"route_{template_name}_{self._route_counter}"

            logger.debug(
                f"Registering route: {route_path} -> {template_name}.html (endpoint: {endpoint_name})"
            )

            # Create assets folder for this template if it doesn't exist
            self._create_template_assets_folder(template_name)

            def create_route_handler(template):
                engine_self = self

                def route_handler():
                    logger.debug(f"Serving template {template}")
                    try:
                        # Load template configuration
                        template_config = copy.deepcopy(
                            engine_self.template_config_parser.load_config(template)
                        )

                        preview_token = request.args.get("__preview_token")
                        mycelian_preview_mode = False
                        overrides: Dict[str, Any] = {}
                        if preview_token:
                            with engine_self._preview_sessions_lock:
                                sess = engine_self._preview_sessions.get(
                                    preview_token
                                )
                            if isinstance(sess, dict) and sess.get("template") == template:
                                ov = sess.get("overrides")
                                if isinstance(ov, dict):
                                    overrides = ov
                                    mycelian_preview_mode = True

                        if overrides:
                            for element in template_config.get("elements", []):
                                if not isinstance(element, dict):
                                    continue
                                eid = element.get("id")
                                if eid in overrides:
                                    element["value"] = overrides[eid]

                        # Convert config elements to template variables
                        template_vars = {}
                        if "elements" in template_config:
                            for element in template_config["elements"]:
                                if "id" in element and "value" in element:
                                    template_vars[element["id"]] = element["value"]

                        mycelian_preview_profile = resolve_template_preview_profile(
                            template, template_config
                        )

                        logger.debug(
                            f"Template {template} variables: {list(template_vars.keys())}"
                        )
                        return render_template(
                            f"{template}.html",
                            **template_vars,
                            mycelian_html_stem=str(template),
                            mycelian_preview_mode=mycelian_preview_mode,
                            mycelian_preview_profile=mycelian_preview_profile,
                        )
                    except Exception as e:
                        logger.error(
                            f"Error rendering template {template}: {str(e)}",
                            exc_info=True,
                        )
                        return f"Error loading template {template}: {str(e)}", 500

                return route_handler

            # Register the route with Flask
            route_handler = create_route_handler(template_name)
            self.app.add_url_rule(
                route_path, endpoint_name, route_handler, methods=["GET"]
            )

            logger.debug(f"Successfully registered route for template: {template_name}")

        except Exception as e:
            logger.error(
                f"Error registering route for template {template_name}: {str(e)}",
                exc_info=True,
            )

    def _create_template_assets_folder(self, template_name):
        """Create assets folder for a template if it doesn't exist"""
        try:
            # Create main assets directory if it doesn't exist
            assets_dir = os.path.abspath("assets")
            if not os.path.exists(assets_dir):
                os.makedirs(assets_dir)
                logger.debug(f"Created main assets directory: {assets_dir}")

            # Create template-specific folder within assets
            template_assets_dir = os.path.join(assets_dir, template_name)
            if not os.path.exists(template_assets_dir):
                os.makedirs(template_assets_dir)
                logger.debug(
                    f"Created assets folder for template '{template_name}': {template_assets_dir}"
                )
            else:
                logger.debug(
                    f"Assets folder already exists for template '{template_name}': {template_assets_dir}"
                )

        except Exception as e:
            logger.error(
                f"Error creating assets folder for template {template_name}: {str(e)}",
                exc_info=True,
            )

    def push_preview_overrides(
        self, token: str, template_name: str, overrides: Dict[str, Any]
    ) -> None:
        """
        Store unsaved form values for the Custom Sources iframe preview.

        The template GET handler merges ``overrides`` into the loaded JSON when
        ``__preview_token`` matches and ``template_name`` equals the route template.
        """
        if not token or not template_name:
            return
        safe = dict(overrides) if isinstance(overrides, dict) else {}
        with self._preview_sessions_lock:
            self._preview_sessions[token] = {
                "template": template_name,
                "overrides": safe,
                "ts": time.time(),
            }

    def _rebuild_url_map(self):
        """Rebuild the entire URL map to handle removed routes"""
        try:
            logger.info("Rebuilding Flask URL map for template routes...")

            # Store the original URL map rules (non-template routes)
            original_rules = []
            template_rule_endpoints = set()

            # Identify which rules are template routes vs system routes
            for rule in list(self.app.url_map.iter_rules()):
                if rule.endpoint.startswith("route_"):
                    template_rule_endpoints.add(rule.endpoint)
                else:
                    # Keep system routes (static files, debug, etc.)
                    original_rules.append(rule)

            # Create a new URL map
            from werkzeug.routing import Map

            new_url_map = Map()

            # Add back the original system routes
            for rule in original_rules:
                new_url_map.add(rule)

            # Replace the app's URL map
            self.app.url_map = new_url_map

            # Clear our tracking and re-register all current templates
            self._registered_template_routes.clear()
            self.register_routes()

            logger.info("Flask URL map rebuilt successfully")

        except Exception as e:
            logger.error(f"Error rebuilding URL map: {str(e)}", exc_info=True)

    def register_socket_events(self):
        """Register SocketIO event handlers"""

        @self.socketio.on("get_audio_files")
        def handle_get_audio_files(data):
            """
            Handle get_audio_files websocket event.
            Lists audio files in a specified folder for random selection.

            Args:
                data (dict): Dictionary containing:
                    - folder (str): The folder name under /assets/alerts/
                    - request_id (str): Unique request identifier for response matching
            """
            client_sid = request.sid
            logger.debug(f"Received get_audio_files request from {client_sid}: {data}")
            print(f"Received get_audio_files request from {client_sid}: {data}")

            try:
                if (
                    not isinstance(data, dict)
                    or "folder" not in data
                    or "request_id" not in data
                ):
                    logger.error("Invalid data format for get_audio_files")
                    response_data = {
                        "success": False,
                        "error": "Invalid data format: folder and request_id required",
                        "request_id": data.get("request_id", "unknown"),
                        "files": [],
                    }
                    self.socketio.emit(
                        "audio_files_response", response_data, to=client_sid
                    )
                    return

                folder_name = data["folder"]
                request_id = data["request_id"]

                # Sanitize folder name to prevent directory traversal while allowing subdirectories
                # Remove dangerous patterns but keep legitimate path separators
                folder_name = folder_name.replace("..", "").replace("\\", "/")
                # Remove leading/trailing slashes and normalize multiple slashes
                folder_name = folder_name.strip("/").replace("//", "/")

                # Ensure we don't escape the assets/alerts directory
                if folder_name.startswith("/") or ".." in folder_name:
                    logger.warning(f"Invalid folder path attempted: {folder_name}")
                    response_data = {
                        "success": False,
                        "error": "Invalid folder path",
                        "request_id": request_id,
                        "files": [],
                    }
                    self.socketio.emit(
                        "audio_files_response", response_data, to=client_sid
                    )
                    return

                # Build the full path to the audio folder
                audio_folder_path = os.path.join("assets", "alerts", folder_name)

                # Check if the folder exists
                if not os.path.exists(audio_folder_path):
                    logger.warning(f"Audio folder not found: {audio_folder_path}")
                    response_data = {
                        "success": False,
                        "error": f"Audio folder not found: {folder_name}",
                        "request_id": request_id,
                        "files": [],
                    }
                    self.socketio.emit(
                        "audio_files_response", response_data, to=client_sid
                    )
                    return

                # Get list of audio files in the folder
                audio_extensions = [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"]
                audio_files = []

                try:
                    for filename in os.listdir(audio_folder_path):
                        file_path = os.path.join(audio_folder_path, filename)
                        # Check if it's a file and has an audio extension
                        if os.path.isfile(file_path):
                            _, ext = os.path.splitext(filename.lower())
                            if ext in audio_extensions:
                                audio_files.append(filename)

                    audio_files.sort()  # Sort for consistent ordering

                    logger.debug(
                        f"Found {len(audio_files)} audio files in {audio_folder_path}"
                    )

                    response_data = {
                        "success": True,
                        "request_id": request_id,
                        "files": audio_files,
                        "folder": folder_name,
                    }
                    self.socketio.emit(
                        "audio_files_response", response_data, to=client_sid
                    )

                except Exception as e:
                    logger.error(
                        f"Error listing files in {audio_folder_path}: {str(e)}",
                        exc_info=True,
                    )
                    response_data = {
                        "success": False,
                        "error": f"Error reading folder: {str(e)}",
                        "request_id": request_id,
                        "files": [],
                    }
                    self.socketio.emit(
                        "audio_files_response", response_data, to=client_sid
                    )

            except Exception as e:
                logger.error(f"Error handling get_audio_files: {str(e)}", exc_info=True)
                response_data = {
                    "success": False,
                    "error": str(e),
                    "request_id": data.get("request_id", "unknown"),
                    "files": [],
                }
                self.socketio.emit("audio_files_response", response_data, to=client_sid)

        @self.socketio.on("connect")
        def handle_connect():
            print(f" WEBSOCKET: Client connected - SID: {request.sid}")
            logger.debug(f"Client connected: {request.sid}")

            # Send initial pause status to the newly connected client
            try:
                global ALERTS_PAUSED
                logger.debug(
                    f"Sending initial pause status to {request.sid}: paused={ALERTS_PAUSED}"
                )

                # Send the current pause status immediately
                self.socketio.emit(
                    "pause_status_update", {"paused": ALERTS_PAUSED}, to=request.sid
                )

                # Also send the specific paused/resumed event for templates that only listen for these
                if ALERTS_PAUSED:
                    self.socketio.emit(
                        "alerts_paused", {"paused": True}, to=request.sid
                    )
                else:
                    self.socketio.emit(
                        "alerts_resumed", {"paused": False}, to=request.sid
                    )

                logger.debug(f"Successfully sent initial pause status to {request.sid}")
            except Exception as e:
                logger.error(
                    f"Error sending initial pause status to {request.sid}: {str(e)}",
                    exc_info=True,
                )

            # Send current theme to the newly connected client
            try:
                theme_mgr = get_theme_manager()
                theme = theme_mgr.get_theme()
                if theme:
                    theme_css = generate_css_variables(theme)
                    self.socketio.emit(
                        "theme_update",
                        {
                            "css": theme_css,
                            "theme_name": theme.name,
                            "theme_type": theme.theme_type or "dark",
                        },
                        to=request.sid,
                    )
                    logger.debug(
                        f"Sent initial theme to {request.sid}: {theme.name}"
                    )
            except Exception as e:
                logger.error(
                    f"Error sending initial theme to {request.sid}: {str(e)}",
                    exc_info=True,
                )

        @self.socketio.on("get_current_theme")
        def handle_get_current_theme():
            """Send the current theme to the requesting client."""
            try:
                theme_mgr = get_theme_manager()
                theme = theme_mgr.get_theme()
                if theme:
                    theme_css = generate_css_variables(theme)
                    self.socketio.emit(
                        "theme_update",
                        {
                            "css": theme_css,
                            "theme_name": theme.name,
                            "theme_type": theme.theme_type or "dark",
                        },
                        to=request.sid,
                    )
                    logger.debug(
                        f"Sent current theme to {request.sid}: {theme.name}"
                    )
            except Exception as e:
                logger.error(
                    f"Error handling get_current_theme: {str(e)}",
                    exc_info=True,
                )

        @self.socketio.on("disconnect")
        def handle_disconnect():
            print(f" WEBSOCKET: Client disconnected - SID: {request.sid}")
            logger.debug(f"Client disconnected: {request.sid}")

        @self.socketio.on("game_hook_command")
        def handle_game_hook_command(data):
            """Template → server commands for game hooks (e.g. clear boss list)."""
            try:
                from .game_hooks_service import handle_game_hook_command as _run_hook_cmd

                _run_hook_cmd(data)
            except Exception as e:
                logger.error(
                    f"Error handling game_hook_command: {str(e)}", exc_info=True
                )

        @self.socketio.on("alert_complete")
        def handle_alert_complete():
            global ALERT_PLAYING
            ALERT_PLAYING = False
            logger.debug("Alert completed, ALERT_PLAYING set to False")

        @self.socketio.on("pause_alerts")
        def handle_pause_alerts():
            # Delegate to the main toggle_alerts method for consistency
            self.toggle_alerts()

        @self.socketio.on("toggle_alerts")
        def handle_toggle_alerts():
            # Handle the legacy toggle_alerts event for backward compatibility
            self.toggle_alerts()

        @self.socketio.on("set_data")
        def handle_data_set(data):
            """
            Handle set_data websocket event

            Args:
                data (dict): Dictionary containing:
                    - path (str): The path in the database to set the data
                    - data (dict): The data to set at the specified path
            """
            try:
                if (
                    not isinstance(data, dict)
                    or "path" not in data
                    or "data" not in data
                ):
                    logger.error("Invalid data format received in set_data event")
                    return False

                path = data["path"]
                data_to_set = data["data"]

                database_manager.set_data(path, data_to_set)
                self._broadcast_source_control_runtime_path_update(path, data_to_set)
                logger.debug(f"Successfully set data at path: {path}")
                return True
            except Exception as e:
                logger.error(f"Error setting data: {str(e)}", exc_info=True)
                return False

        @self.socketio.on("update_data")
        def handle_data_update(data):
            """
            Handle update_data websocket event

            Args:
                data (dict): Dictionary containing:
                    - path (str): The path in the database to update the data
                    - data (dict): The data to update at the specified path
            """
            try:
                if (
                    not isinstance(data, dict)
                    or "path" not in data
                    or "data" not in data
                ):
                    logger.error("Invalid data format received in update_data event")
                    return False

                path = data["path"]
                data_to_update = data["data"]

                database_manager.update_data(path, data_to_update)
                self._broadcast_source_control_runtime_path_update(path, data_to_update)
                logger.debug(f"Successfully updated data at path: {path}")
                return True
            except Exception as e:
                logger.error(f"Error updating data: {str(e)}", exc_info=True)
                return False

        @self.socketio.on("get_data")
        def handle_data_get(data):
            """
            Handle get_data websocket event

            Args:
                data (dict): Dictionary containing:
                    - path (str): The path in the database to get the data from
                    - request_etag (bool, optional): Whether to request the etag with the data
            """
            try:
                if not isinstance(data, dict) or "path" not in data:
                    logger.error("Invalid data format received in get_data event")
                    self.socketio.emit(
                        "get_data", {"error": "Invalid data format"}, to=request.sid
                    )
                    return False

                path = data["path"]
                request_etag = data.get("request_etag", False)

                result = database_manager.get_data(path, request_etag)
                logger.debug(f"Successfully retrieved data from path: {path}")

                # Emit the result back to the client
                self.socketio.emit("get_data", result, to=request.sid)
                return result
            except Exception as e:
                logger.error(f"Error getting data: {str(e)}", exc_info=True)
                self.socketio.emit("get_data", {"error": str(e)}, to=request.sid)
                return False

        @self.socketio.on("get_alert_state")
        def handle_get_alert_state(data=None):
            """
            Handle get_alert_state websocket event to retrieve the current alert configuration state

            Args:
                data (dict, optional): Dictionary containing optional parameters:
                    - alert_type (str, optional): Specific alert type to retrieve
                    - include_ranges (bool, optional): Whether to include range alerts (default: True)

            Returns:
                dict: The alert state data
            """
            client_sid = request.sid
            try:
                # Initialize the alert state manager if not already done
                alertutils.initialize_alert_state()

                # Check for specific alert type request
                if data and isinstance(data, dict) and "alert_type" in data:
                    alert_type = data["alert_type"]
                    include_ranges = data.get("include_ranges", True)

                    # Get specific alert type data
                    result = alertutils.alert_state_manager.get_alerts_by_type(
                        alert_type, include_ranges=include_ranges
                    )
                    logger.debug(
                        f"Returning alert state for type: {alert_type} to {client_sid}"
                    )
                else:
                    # Get all alert data
                    result = alertutils.alert_state_manager.get_all_alerts()
                    logger.debug(f"Returning complete alert state to {client_sid}")

                # Emit the result back to the requesting client
                self.socketio.emit("alert_state_response", result, to=client_sid)
                logger.debug(
                    f"Emitted alert_state_response to {client_sid} with {len(result) if result else 0} alerts"
                )

                return result
            except Exception as e:
                logger.error(f"Error getting alert state: {str(e)}", exc_info=True)
                error_result = {"error": str(e)}
                self.socketio.emit("alert_state_response", error_result, to=client_sid)
                return error_result

        @self.socketio.on("update_alert_data")
        def handle_update_alert_data(data):
            """
            Handle update_alert_data websocket event to update alert data through AlertStateManager

            Args:
                data (dict): Dictionary containing:
                    - alert_id (str): The alert ID (format: "bits100", "subs1", etc.)
                    - alert_data (dict): The alert data to update

            Returns:
                dict: Success/error response
            """
            try:
                if (
                    not isinstance(data, dict)
                    or "alert_id" not in data
                    or "alert_data" not in data
                ):
                    logger.error(
                        "Invalid data format received in update_alert_data event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: alert_id and alert_data required",
                    }

                alert_id = data["alert_id"]
                alert_data = data["alert_data"]

                # Initialize the alert state manager if not already done
                alertutils.initialize_alert_state()

                # Update the alert data
                success = alertutils.alert_state_manager.update_alert_data(
                    alert_id, alert_data
                )

                if success:
                    logger.debug(f"Successfully updated alert data for {alert_id}")
                    return {"success": True}
                else:
                    logger.error(f"Failed to update alert data for {alert_id}")
                    return {"success": False, "error": "Failed to update alert data"}

            except Exception as e:
                logger.error(f"Error updating alert data: {str(e)}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.socketio.on("save_alert_by_id")
        def handle_save_alert_by_id(data):
            """
            Handle save_alert_by_id websocket event to save current alert data to Firebase

            Args:
                data (dict): Dictionary containing:
                    - alert_id (str): The alert ID (format: "bits100", "subs1", etc.)

            Returns:
                dict: Success/error response
            """
            try:
                if not isinstance(data, dict) or "alert_id" not in data:
                    logger.error(
                        "Invalid data format received in save_alert_by_id event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: alert_id required",
                    }

                alert_id = data["alert_id"]

                # Initialize the alert state manager if not already done
                alertutils.initialize_alert_state()

                # Save the alert by ID
                success = alertutils.alert_state_manager.save_alert_by_id(alert_id)

                if success:
                    logger.debug(f"Successfully saved alert {alert_id}")
                    return {"success": True}
                else:
                    logger.error(f"Failed to save alert {alert_id}")
                    return {"success": False, "error": "Failed to save alert"}

            except Exception as e:
                logger.error(f"Error saving alert by ID: {str(e)}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.socketio.on("get_stored_alerts")
        def handle_get_stored_alerts(data=None):
            """
            Handle get_stored_alerts websocket event to retrieve completed/stored alerts

            Returns:
                dict: Dictionary of stored alerts
            """
            try:
                # Initialize the alert state manager if not already done
                alertutils.initialize_alert_state()

                # Get stored alerts
                result = alertutils.alert_state_manager.get_stored_alerts()
                logger.debug(f"Returning {len(result)} stored alerts")

                return result
            except Exception as e:
                logger.error(f"Error getting stored alerts: {str(e)}", exc_info=True)
                return {}

        @self.socketio.on("twitch_api_proxy")
        def handle_twitch_api_proxy(data):
            """
            Handle twitch_api_proxy websocket event.
            Allows client to make generic calls to the Twitch API.

            Args:
                data (dict): Dictionary containing:
                    - url (str): The full Twitch API URL to call.
                    - method (str, optional): HTTP method (GET, POST, etc.). Defaults to "GET".
                    - params (dict, optional): Query parameters.
                    - json_data (dict, optional): JSON body for the request.
            """
            # Store the client session ID before entering the thread
            client_sid = request.sid
            logger.debug(f"Received twitch_api_proxy request: {data}")
            try:
                if not isinstance(data, dict) or "url" not in data:
                    logger.error(
                        "Invalid data format for twitch_api_proxy: 'url' is required."
                    )
                    self.socketio.emit(
                        "twitch_api_proxy_response",
                        {
                            "error": "Invalid data format: 'url' is required.",
                            "success": False,
                        },
                        to=client_sid,
                    )
                    return

                url = data["url"]
                method = data.get("method", "GET").upper()
                params = data.get("params")
                json_payload = data.get(
                    "json_data"
                )  # Renamed to avoid conflict with aiohttp's 'json' parameter if passed directly

                # Run the async handler in a new thread with its own event loop
                def run_async_handler():
                    try:
                        # Create a new event loop for this thread
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                        async def handle_request():
                            try:
                                if not twitch.twitch_api:
                                    logger.error(
                                        "Twitch API module not initialized or twitch_api instance not found."
                                    )
                                    self.socketio.emit(
                                        "twitch_api_proxy_response",
                                        {
                                            "error": "Twitch API not initialized.",
                                            "success": False,
                                        },
                                        to=client_sid,
                                    )
                                    return

                                # Get current Twitch authentication data from state manager
                                from .dataobjects import state_manager

                                twitch_data = state_manager.get_twitch_data()

                                # Check if we have valid authentication credentials from state manager
                                if (
                                    not twitch_data
                                    or not twitch_data.auth_token
                                    or not twitch_data.client_id
                                ):
                                    logger.warning(
                                        "Twitch API not authenticated - no valid tokens in state manager"
                                    )
                                    self.socketio.emit(
                                        "twitch_api_proxy_response",
                                        {
                                            "error": "Twitch authentication required.",
                                            "success": False,
                                        },
                                        to=client_sid,
                                    )
                                    return

                                # Update the twitch API instance with current state manager data if needed
                                if (
                                    twitch.twitch_api.auth_token
                                    != twitch_data.auth_token
                                    or twitch.twitch_api.client_id
                                    != twitch_data.client_id
                                ):
                                    logger.debug(
                                        "Updating Twitch API instance with current state manager data for proxy call"
                                    )
                                    twitch.twitch_api.auth_token = (
                                        twitch_data.auth_token
                                    )
                                    twitch.twitch_api.client_id = twitch_data.client_id
                                    twitch.twitch_api.refresh_token = (
                                        twitch_data.refresh_token
                                    )
                                    if twitch_data.token_expiry:
                                        try:
                                            twitch.twitch_api.token_expiry = (
                                                datetime.fromisoformat(
                                                    twitch_data.token_expiry
                                                )
                                            )
                                        except ValueError:
                                            logger.warning(
                                                f"Invalid token expiry format in state manager: {twitch_data.token_expiry}"
                                            )
                                            twitch.twitch_api.token_expiry = None

                                # Store the original tokens to detect if they were refreshed
                                original_auth_token = twitch.twitch_api.auth_token
                                original_refresh_token = twitch.twitch_api.refresh_token

                                # Call the generic_api_call method
                                api_response = await twitch.twitch_api.generic_api_call(
                                    url=url,
                                    method=method,
                                    params=params,
                                    json_data=json_payload,
                                )

                                # Check if tokens were refreshed during the API call
                                if (
                                    twitch.twitch_api.auth_token != original_auth_token
                                    or twitch.twitch_api.refresh_token
                                    != original_refresh_token
                                ):
                                    logger.info(
                                        "Tokens were refreshed during API proxy call - syncing to state manager"
                                    )

                                    # Sync the refreshed tokens back to state manager
                                    try:
                                        refreshed_twitch_data = {
                                            "client_id": twitch.twitch_api.client_id,
                                            "client_secret": twitch.twitch_api.client_secret,
                                            "auth_token": twitch.twitch_api.auth_token,
                                            "refresh_token": twitch.twitch_api.refresh_token,
                                            "user_id": twitch.twitch_api.user_id,
                                            "token_expiry": (
                                                twitch.twitch_api.token_expiry.isoformat()
                                                if twitch.twitch_api.token_expiry
                                                else ""
                                            ),
                                            "current_category": twitch_data.current_category,  # Preserve existing category
                                        }

                                        state_manager.set_twitch_data(
                                            refreshed_twitch_data
                                        )
                                        save_success = state_manager.save_changes()

                                        if save_success:
                                            logger.info(
                                                "Successfully synced refreshed tokens from API proxy call to state manager"
                                            )
                                        else:
                                            logger.warning(
                                                "Failed to save refreshed tokens from API proxy call to state manager"
                                            )

                                    except Exception as sync_error:
                                        logger.error(
                                            f"Error syncing refreshed tokens from API proxy call: {str(sync_error)}",
                                            exc_info=True,
                                        )

                                logger.debug(
                                    f"Twitch API proxy call successful for URL: {url}"
                                )
                                self.socketio.emit(
                                    "twitch_api_proxy_response",
                                    {"success": True, "data": api_response},
                                    to=client_sid,
                                )

                            except Exception as e:
                                logger.error(
                                    f"Error in async twitch_api_proxy handler: {str(e)}",
                                    exc_info=True,
                                )
                                self.socketio.emit(
                                    "twitch_api_proxy_response",
                                    {"error": str(e), "success": False},
                                    to=client_sid,
                                )

                        # Run the async function
                        loop.run_until_complete(handle_request())

                    except Exception as e:
                        logger.error(
                            f"Error in thread for twitch_api_proxy: {str(e)}",
                            exc_info=True,
                        )
                        self.socketio.emit(
                            "twitch_api_proxy_response",
                            {"error": str(e), "success": False},
                            to=client_sid,
                        )
                    finally:
                        # Clean up the event loop
                        try:
                            loop.close()
                        except:
                            pass

                # Start the thread
                import threading

                thread = threading.Thread(target=run_async_handler, daemon=True)
                thread.start()

            except Exception as e:
                logger.error(f"Error in twitch_api_proxy: {str(e)}", exc_info=True)
                self.socketio.emit(
                    "twitch_api_proxy_response",
                    {"error": str(e), "success": False},
                    to=client_sid,
                )

        @self.socketio.on("get_psn_data")
        def handle_get_psn_data(
            data=None,
        ):  # data arg can be used for future specific requests
            """
            Handle get_psn_data websocket event.
            Retrieves the latest PSN data from StateManager and sends it to the client.
            """
            client_sid = request.sid
            logger.debug(
                f"=== WEB ENGINE: Received get_psn_data request from {client_sid} ==="
            )
            logger.debug(f"Request data: {data}")
            logger.debug(f"Request timestamp: {datetime.now().isoformat()}")

            try:
                logger.debug("Attempting to get live PSN data from state manager...")
                live_psn_data = state_manager.get_live_psn_data()
                logger.debug(f"Live PSN data retrieved: {live_psn_data}")

                if live_psn_data:
                    # Convert dataclass to dict for JSON serialization
                    psn_data_dict = dataclasses.asdict(live_psn_data)
                    logger.debug(f"PSN data converted to dict: {psn_data_dict}")
                    logger.debug(f"PSN data dict keys: {list(psn_data_dict.keys())}")

                    # Log specific fields that the template is looking for
                    logger.debug("=== PSN Data Fields for Trophies Template ===")
                    logger.debug(
                        f"  - current_game_name: {psn_data_dict.get('current_game_name')}"
                    )
                    logger.debug(
                        f"  - current_game_art_url: {psn_data_dict.get('current_game_art_url')}"
                    )
                    logger.debug(
                        f"  - current_game_trophies: {psn_data_dict.get('current_game_trophies')}"
                    )
                    logger.debug(
                        f"  - trophy_counts: {psn_data_dict.get('trophy_counts')}"
                    )
                    logger.debug(
                        f"  - current_game_progress: {psn_data_dict.get('current_game_progress')}"
                    )

                    self.socketio.emit("psn_data_update", psn_data_dict, to=client_sid)
                    logger.debug(f"Successfully sent psn_data_update to {client_sid}")
                    logger.debug(
                        f"Data sent - Current game: {live_psn_data.current_game_name}"
                    )
                else:
                    logger.warning("No live PSN data available - sending default data")
                    # Send a default/empty state if no data is available
                    default_data_dict = dataclasses.asdict(
                        PSNData()
                    )  # Send a default empty PSNData structure
                    logger.debug(f"Default PSN data dict: {default_data_dict}")
                    self.socketio.emit(
                        "psn_data_update", default_data_dict, to=client_sid
                    )
                    logger.debug(
                        f"Sent default psn_data_update to {client_sid} as no live data was available."
                    )

                logger.debug("=== WEB ENGINE: get_psn_data handling complete ===")
                return True  # Acknowledge successful handling
            except Exception as e:
                logger.error(f"=== WEB ENGINE: Error handling get_psn_data ===")
                logger.error(f"Error details: {str(e)}", exc_info=True)
                logger.error(f"Client: {client_sid}")
                logger.error(f"Timestamp: {datetime.now().isoformat()}")
                # Optionally emit an error event back to the client
                self.socketio.emit("psn_data_error", {"error": str(e)}, to=client_sid)
                return False

        @self.socketio.on("pause-loaded")
        def handle_pause_loaded():
            """
            Handle pause-loaded websocket event.
            Emitted by pausedalerts template when it loads.
            """
            client_sid = request.sid
            logger.debug(f"Pause alerts template loaded from {client_sid}")
            # Send current pause status to the newly loaded template
            global ALERTS_PAUSED
            self.socketio.emit(
                "pause_status_update", {"paused": ALERTS_PAUSED}, to=client_sid
            )

        @self.socketio.on("mycelian_preview_emit_activity_feed")
        def handle_mycelian_preview_emit_activity_feed():
            """Custom Sources iframe only: replay production-shaped activity_feed_alert payloads."""
            sid = request.sid
            try:
                from modules.uiwindows.activity_feed import iter_activity_feed_preview_payloads

                for payload in iter_activity_feed_preview_payloads():
                    self.socketio.emit("activity_feed_alert", payload, to=sid)
            except Exception as e:
                logger.warning(
                    "mycelian_preview_emit_activity_feed failed: %s", e, exc_info=True
                )

        @self.socketio.on("mycelian_preview_emit_alerts")
        def handle_mycelian_preview_emit_alerts():
            """Same path as connector ``alerts_play_alert`` → template listener."""
            sid = request.sid
            self.socketio.emit("alerts_play_alert", None, to=sid)

        @self.socketio.on("mycelian_preview_emit_chat")
        def handle_mycelian_preview_emit_chat():
            sid = request.sid
            self.socketio.emit("chat_add_message", None, to=sid)
            self.socketio.emit(
                "chat_add_message",
                {
                    "username": "PreviewViewer",
                    "message": "Another sample line for preview.",
                    "color": "#1E90FF",
                    "timestamp": time.time(),
                    "id": "mycelian-preview-b",
                },
                to=sid,
            )

        @self.socketio.on("mycelian_preview_emit_pausedalerts")
        def handle_mycelian_preview_emit_pausedalerts():
            """Show paused UI in preview without broadcasting global pause state."""
            sid = request.sid
            self.socketio.emit("pause_status_update", {"paused": True}, to=sid)

        @self.socketio.on("get_pause_status")
        def handle_get_pause_status():
            """
            Handle get_pause_status websocket event.
            Returns the current pause status to the requesting client.
            """
            client_sid = request.sid
            global ALERTS_PAUSED
            logger.debug(f"Received get_pause_status request from {client_sid}")
            self.socketio.emit(
                "pause_status_update", {"paused": ALERTS_PAUSED}, to=client_sid
            )
            return {"paused": ALERTS_PAUSED}

        @self.socketio.on("set_pause_status")
        def handle_set_pause_status(data):
            """
            Handle set_pause_status websocket event.
            Allows clients to set the pause status.

            Args:
                data (dict): Dictionary containing:
                    - paused (bool): Whether alerts should be paused
            """
            global ALERTS_PAUSED
            print("set_pause_status called")
            try:
                if not isinstance(data, dict) or "paused" not in data:
                    logger.error(
                        "Invalid data format received in set_pause_status event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: paused boolean required",
                    }

                new_status = bool(data["paused"])
                old_status = ALERTS_PAUSED
                ALERTS_PAUSED = new_status
                print(f"ALERTS_PAUSED set to {ALERTS_PAUSED}")

                logger.debug(
                    f"Pause status changed from {old_status} to {new_status} by {request.sid}"
                )

                # Broadcast the status change to all connected clients
                if new_status:
                    self.socketio.emit("alerts_paused", {"paused": True})
                    print("alerts_paused emitted")
                else:
                    self.socketio.emit("alerts_resumed", {"paused": False})
                    print("alerts_resumed emitted")
                # Also send the general status update
                self.socketio.emit("pause_status_update", {"paused": new_status})
                print("pause_status_update emitted")
                return {"success": True, "paused": new_status}
            except Exception as e:
                logger.error(f"Error setting pause status: {str(e)}", exc_info=True)
                return {"success": False, "error": str(e)}

        @self.socketio.on("get_spotify_data")
        def handle_get_spotify_data(data=None):
            """
            Handle get_spotify_data websocket event.
            Retrieves the current Spotify data from StateManager and sends it to the client.
            """
            logger.debug(f"Received get_spotify_data request from {request.sid}")
            try:
                from . import spotify
                from .dataobjects import state_manager

                # Get current Spotify data from state manager
                spotify_data = state_manager.get_spotify_data()

                if spotify_data:
                    # Convert dataclass to dict for JSON serialization
                    import dataclasses

                    spotify_data_dict = dataclasses.asdict(spotify_data)
                    self.socketio.emit(
                        "spotify_data_update", spotify_data_dict, to=request.sid
                    )
                    logger.debug(
                        f"Sent spotify_data_update to {request.sid}: {spotify_data.track_name}"
                    )
                else:
                    # Send empty data if no data is available
                    import dataclasses

                    from .dataobjects import SpotifyData

                    default_data_dict = dataclasses.asdict(SpotifyData())
                    self.socketio.emit(
                        "spotify_data_update", default_data_dict, to=request.sid
                    )
                    logger.debug(f"Sent default spotify_data_update to {request.sid}")
                return True
            except Exception as e:
                logger.error(
                    f"Error handling get_spotify_data: {str(e)}", exc_info=True
                )
                self.socketio.emit(
                    "spotify_data_error", {"error": str(e)}, to=request.sid
                )
                return False

        @self.socketio.on("spotify_oauth_start")
        def handle_spotify_oauth_start(data=None):
            """
            Handle spotify_oauth_start websocket event.
            Starts the Spotify OAuth flow and returns the authorization URL.
            """
            logger.debug(f"Received spotify_oauth_start request from {request.sid}")
            try:
                from . import spotify

                auth_url = spotify.trigger_oauth_flow()
                if auth_url:
                    self.socketio.emit(
                        "spotify_oauth_url", {"url": auth_url}, to=request.sid
                    )
                    logger.debug(f"Sent Spotify OAuth URL to {request.sid}")
                    return {"success": True, "url": auth_url}
                else:
                    self.socketio.emit(
                        "spotify_oauth_error",
                        {"error": "Failed to generate OAuth URL"},
                        to=request.sid,
                    )
                    return {"success": False, "error": "Failed to generate OAuth URL"}
            except Exception as e:
                logger.error(
                    f"Error handling spotify_oauth_start: {str(e)}", exc_info=True
                )
                self.socketio.emit(
                    "spotify_oauth_error", {"error": str(e)}, to=request.sid
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("spotify_oauth_complete")
        def handle_spotify_oauth_complete(data):
            """
            Handle spotify_oauth_complete websocket event.
            Completes the Spotify OAuth flow with the provided authorization code.

            Args:
                data (dict): Dictionary containing:
                    - code (str): The authorization code from Spotify
            """
            logger.debug(f"Received spotify_oauth_complete request from {request.sid}")
            try:
                if not isinstance(data, dict) or "code" not in data:
                    logger.error(
                        "Invalid data format received in spotify_oauth_complete event"
                    )
                    self.socketio.emit(
                        "spotify_oauth_error",
                        {"error": "Invalid data format: code required"},
                        to=request.sid,
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: code required",
                    }

                from . import spotify

                success = spotify.complete_oauth_with_code(data["code"])
                if success:
                    self.socketio.emit(
                        "spotify_oauth_success",
                        {"message": "Successfully connected to Spotify"},
                        to=request.sid,
                    )
                    logger.info(
                        f"Spotify OAuth completed successfully for {request.sid}"
                    )
                    return {"success": True}
                else:
                    self.socketio.emit(
                        "spotify_oauth_error",
                        {"error": "Failed to complete OAuth flow"},
                        to=request.sid,
                    )
                    return {"success": False, "error": "Failed to complete OAuth flow"}
            except Exception as e:
                logger.error(
                    f"Error handling spotify_oauth_complete: {str(e)}", exc_info=True
                )
                self.socketio.emit(
                    "spotify_oauth_error", {"error": str(e)}, to=request.sid
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("update_spotify_settings")
        def handle_update_spotify_settings(data):
            """
            Handle update_spotify_settings websocket event.
            Updates Spotify settings through the Spotify module.

            Args:
                data (dict): Dictionary containing Spotify settings to update
            """
            logger.debug(f"Received update_spotify_settings request from {request.sid}")
            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format received in update_spotify_settings event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: dictionary required",
                    }

                from . import spotify

                # Extract settings
                client_id = data.get("client_id", "")
                client_secret = data.get("client_secret", "")

                # Update settings
                spotify.update_spotify_settings(
                    client_id=client_id, client_secret=client_secret
                )

                logger.debug(f"Updated Spotify settings for {request.sid}")
                return {"success": True}
            except Exception as e:
                logger.error(
                    f"Error handling update_spotify_settings: {str(e)}", exc_info=True
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("get_spotify_status")
        def handle_get_spotify_status(data=None):
            """
            Handle get_spotify_status websocket event.
            Returns the current Spotify connection status.
            """
            logger.debug(f"Received get_spotify_status request from {request.sid}")
            try:
                from . import spotify

                status = spotify.get_spotify_status()
                self.socketio.emit("spotify_status_update", status, to=request.sid)
                logger.debug(
                    f"Sent Spotify status to {request.sid}: {status['status']}"
                )
                return status
            except Exception as e:
                logger.error(
                    f"Error handling get_spotify_status: {str(e)}", exc_info=True
                )
                self.socketio.emit(
                    "spotify_status_error", {"error": str(e)}, to=request.sid
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("get_twitch_data")
        def handle_get_twitch_data(data=None):
            """
            Handle get_twitch_data websocket event.
            Retrieves the current Twitch data from StateManager and sends it to the client.
            """
            logger.debug(f"Received get_twitch_data request from {request.sid}")
            try:
                from .dataobjects import state_manager

                # Get current Twitch data from state manager
                twitch_data = state_manager.get_twitch_data()

                if twitch_data:
                    # Convert dataclass to dict for JSON serialization
                    import dataclasses

                    twitch_data_dict = dataclasses.asdict(twitch_data)
                    # Remove sensitive fields before sending
                    sensitive_fields = ["auth_token", "refresh_token", "client_secret"]
                    for field in sensitive_fields:
                        if field in twitch_data_dict:
                            twitch_data_dict[field] = ""

                    self.socketio.emit(
                        "twitch_data_update", twitch_data_dict, to=request.sid
                    )
                    logger.debug(
                        f"Sent twitch_data_update to {request.sid}: Current category {twitch_data.current_category}"
                    )
                else:
                    # Send empty data if no data is available
                    import dataclasses

                    from .dataobjects import TwitchData

                    default_data_dict = dataclasses.asdict(TwitchData())
                    self.socketio.emit(
                        "twitch_data_update", default_data_dict, to=request.sid
                    )
                    logger.debug(f"Sent default twitch_data_update to {request.sid}")
                return True
            except Exception as e:
                logger.error(f"Error handling get_twitch_data: {str(e)}", exc_info=True)
                self.socketio.emit(
                    "twitch_data_error", {"error": str(e)}, to=request.sid
                )
                return False

        @self.socketio.on("get_stored_alerts_paginated")
        def handle_get_stored_alerts_paginated(data):
            """
            Handle get_stored_alerts_paginated websocket event.
            Retrieves paginated stored alerts for the activity feed.

            Args:
                data (dict): Dictionary containing:
                    - page (int): Page number to retrieve
                    - limit (int): Number of alerts per page
            """
            logger.debug(
                f"Received get_stored_alerts_paginated request from {request.sid}"
            )
            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format received in get_stored_alerts_paginated event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: dictionary required",
                    }

                page = data.get("page", 1)
                limit = data.get("limit", 25)

                # Initialize the alert state manager if not already done
                alertutils.initialize_alert_state()

                # Get paginated stored alerts
                result = alertutils.alert_state_manager.get_stored_alerts_paginated(
                    page=page, limit=limit
                )

                # Convert stored alerts to activity feed format
                converted_alerts = []
                for stored_alert in result["alerts"]:
                    # Import the conversion function from activity_feed
                    from .uiwindows.activity_feed import (
                        convert_stored_alert_to_feed_format,
                    )

                    feed_alert = convert_stored_alert_to_feed_format(stored_alert)
                    if feed_alert:
                        converted_alerts.append(feed_alert)

                # Prepare response
                response = {
                    "alerts": converted_alerts,
                    "page": result["page"],
                    "total_pages": result["total_pages"],
                    "total_count": result["total_count"],
                    "has_prev": result["has_prev"],
                    "has_next": result["has_next"],
                }

                self.socketio.emit("stored_alerts_paginated", response, to=request.sid)
                logger.debug(
                    f"Sent {len(converted_alerts)} stored alerts for page {page} to {request.sid}"
                )
                return response
            except Exception as e:
                logger.error(
                    f"Error handling get_stored_alerts_paginated: {str(e)}",
                    exc_info=True,
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "alerts": [],
                    "page": 1,
                    "total_pages": 1,
                    "total_count": 0,
                    "has_prev": False,
                    "has_next": False,
                }
                self.socketio.emit(
                    "stored_alerts_paginated", error_response, to=request.sid
                )
                return error_response

        @self.socketio.on("get_condensed_view_alerts")
        def handle_get_condensed_view_alerts(data):
            """
            Handle get_condensed_view_alerts websocket event.
            Retrieves ALL alerts within the specified time window for condensed view.

            Args:
                data (dict): Dictionary containing:
                    - hours (int): Number of hours to look back
            """
            logger.debug(
                f"Received get_condensed_view_alerts request from {request.sid}"
            )
            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format received in get_condensed_view_alerts event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: dictionary required",
                    }

                hours = data.get("hours", 12)

                # Import necessary modules
                import time

                from modules.uiwindows.activity_feed import load_restored_alerts

                # Calculate time window
                current_time = time.time()
                cutoff_time = current_time - (hours * 3600)  # Convert hours to seconds

                # Load all alerts within the time window
                alerts_to_process = []
                page = 1
                historical_count = 0
                all_alerts_loaded = False

                logger.debug(
                    f"Loading condensed view alerts for past {hours} hours (cutoff: {cutoff_time})"
                )

                while not all_alerts_loaded:
                    restored_alerts, pagination_info = load_restored_alerts(page=page)

                    if not restored_alerts:
                        break

                    # Process alerts from this page
                    page_alerts_in_window = 0
                    for alert_data in restored_alerts:
                        alert_timestamp = alert_data.get("timestamp", 0)
                        if alert_timestamp >= cutoff_time:
                            alerts_to_process.append(alert_data)
                            historical_count += 1
                            page_alerts_in_window += 1
                        else:
                            # If we've reached alerts older than our cutoff time, we can stop
                            # (assuming alerts are returned in chronological order, newest first)
                            all_alerts_loaded = True
                            break

                    # Check if we should continue to next page
                    if not pagination_info.get("has_next", False):
                        all_alerts_loaded = True
                    elif page_alerts_in_window == 0:
                        # If no alerts from this page were in our time window, we can stop
                        all_alerts_loaded = True
                    else:
                        page += 1

                logger.debug(
                    f"Loaded {historical_count} alerts for condensed view (past {hours} hours)"
                )

                response = {
                    "success": True,
                    "alerts": alerts_to_process,
                    "historical_count": historical_count,
                    "hours": hours,
                }

                self.socketio.emit("condensed_view_alerts", response, to=request.sid)
                logger.debug(
                    f"Sent {len(alerts_to_process)} condensed view alerts to {request.sid}"
                )
                return response

            except Exception as e:
                logger.error(
                    f"Error handling get_condensed_view_alerts: {str(e)}", exc_info=True
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "alerts": [],
                    "historical_count": 0,
                    "hours": hours,
                }
                self.socketio.emit(
                    "condensed_view_alerts", error_response, to=request.sid
                )
                return error_response

        @self.socketio.on("activity_feed_replay_alert")
        def handle_activity_feed_replay_alert(data):
            """
            Handle activity_feed_replay_alert websocket event.
            Replays a specific alert from the activity feed by fetching stored data directly from database.

            Args:
                data (dict): Alert data containing alert_id for replay
            """
            logger.debug(
                f"Received activity_feed_replay_alert request from {request.sid}"
            )
            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format received in activity_feed_replay_alert event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: dictionary required",
                    }

                # Get the alert ID from the request data
                alert_id = data.get("alert_id")
                if not alert_id:
                    logger.error("No alert_id found in activity_feed_replay_alert data")
                    return {
                        "success": False,
                        "error": "No alert_id provided for replay",
                    }

                # Log the replay action
                logger.info(f"Replaying alert from activity feed: {alert_id}")

                # Import alertutils and alert_processor for AlertObj creation and queue processing
                from modules import alert_processor, alertutils

                # Fetch stored alert data directly from the database, bypassing any cache systems
                alertutils.alert_state_manager.initialize()
                stored_alert_data = (
                    alertutils.alert_state_manager.get_stored_alert_by_id(alert_id)
                )

                if not stored_alert_data:
                    logger.error(f"No stored alert data found for alert_id: {alert_id}")
                    return {
                        "success": False,
                        "error": f"No stored alert data found for alert_id: {alert_id}",
                    }

                logger.debug(
                    f"Fetched stored alert data for replay: {list(stored_alert_data.keys())}"
                )

                # Create AlertObj and populate all fields from stored data
                replay_alert_obj = alertutils.AlertObj()

                # Copy all available fields from stored alert data to ensure complete AlertObj
                alert_fields = [
                    "duration",
                    "alert_name",
                    "display_name",
                    "alert_type",
                    "deleted",
                    "alert_id",
                    "played",
                    "stackable",
                    "timestamp",
                    "skip_alert",
                    "is_replay",
                    "is_test",
                    "username",
                    "anonymous",
                    "message",
                    "emotes",
                    "title",
                    "tier",
                    "gift_qty",
                    "resub_month",
                    "months_prepaid",
                    "amt_cheered",
                    "twitch_reward_id",
                    "point_cost",
                    "enable_alert",
                    "raider_count",
                    "donation_amount",
                    "currency",
                    "hype_train_level",
                    "hype_train_in_progress",
                    "fade_in",
                    "fade_out",
                    "volume",
                    "audio_only",
                    "single_audio_dir",
                    "single_audio_name",
                    "gif_dir",
                    "gif_name",
                    "randomized",
                    "randomized_dir",
                    "randomized_chance",
                    "randomized_extra",
                    "randomized_extra_chance",
                    "randomized_extra_dir",
                ]

                for field in alert_fields:
                    if (
                        field in stored_alert_data
                        and stored_alert_data[field] is not None
                    ):
                        setattr(replay_alert_obj, field, stored_alert_data[field])
                        logger.debug(
                            f"Set replay alert field {field}: {stored_alert_data[field]}"
                        )

                # Override replay-specific fields
                replay_alert_obj.alert_id = f"Replay{round(time.time())}"
                replay_alert_obj.timestamp = time.time()
                replay_alert_obj.played = False
                replay_alert_obj.stackable = (
                    True  # Make replayed alerts stackable for immediate processing
                )
                replay_alert_obj.is_replay = True  # Mark as replay alert

                logger.debug(
                    f"Created replay AlertObj - type: {replay_alert_obj.alert_type}, "
                    f"gif_dir: {replay_alert_obj.gif_dir}, gif_name: {replay_alert_obj.gif_name}, "
                    f"audio_dir: {replay_alert_obj.single_audio_dir}, audio_name: {replay_alert_obj.single_audio_name}"
                )

                # Append the created AlertObj to the ALERT_QUEUE for processing
                alert_processor.ALERT_QUEUE.append(replay_alert_obj)
                logger.debug(
                    f"Added replay alert to ALERT_QUEUE: {replay_alert_obj.alert_type} (ID: {replay_alert_obj.alert_id})"
                )

                return {"success": True}
            except Exception as e:
                logger.error(
                    f"Error handling activity_feed_replay_alert: {str(e)}",
                    exc_info=True,
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("activity_feed_skip_alert")
        def handle_activity_feed_skip_alert(data):
            """
            Handle activity_feed_skip_alert websocket event.
            Skips/dismisses a specific alert from the activity feed.

            Args:
                data (dict): Alert data to skip
            """
            logger.debug(
                f"Received activity_feed_skip_alert request from {request.sid}"
            )
            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format received in activity_feed_skip_alert event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: dictionary required",
                    }

                # Log the skip action
                logger.info(
                    f"Skipping alert from activity feed: {data.get('type', 'Unknown')} - {data.get('message', 'No message')}"
                )

                # You can add specific skip logic here if needed
                # For now, we'll just acknowledge the action

                return {"success": True}
            except Exception as e:
                logger.error(
                    f"Error handling activity_feed_skip_alert: {str(e)}", exc_info=True
                )
                return {"success": False, "error": str(e)}

        @self.socketio.on("get_route_info")
        def handle_get_route_info(data=None):
            """
            Handle get_route_info websocket event.
            Returns detailed information about registered template routes.
            """
            logger.debug(f"Received get_route_info request from {request.sid}")
            try:
                route_info = self.get_registered_template_routes()
                self.socketio.emit("route_info_update", route_info, to=request.sid)
                logger.debug(
                    f"Sent route info to {request.sid}: {route_info['total_routes']} routes"
                )
                return route_info
            except Exception as e:
                logger.error(f"Error handling get_route_info: {str(e)}", exc_info=True)
                error_response = {"success": False, "error": str(e)}
                self.socketio.emit("route_info_error", error_response, to=request.sid)
                return error_response

        @self.socketio.on("force_route_resync")
        def handle_force_route_resync(data=None):
            """
            Handle force_route_resync websocket event.
            Forces a complete resynchronization of template routes.
            """
            logger.debug(f"Received force_route_resync request from {request.sid}")
            try:
                logger.info("Force route resync triggered by client")

                success = self.force_route_resync()

                if success:
                    # Get updated route info
                    route_info = self.get_registered_template_routes()

                    # Notify all clients about the resync
                    resync_data = {
                        "timestamp": datetime.now().isoformat(),
                        "manual_resync": True,
                        "route_info": route_info,
                    }

                    self.socketio.emit("route_resync_completed", resync_data)

                    logger.info("Force route resync completed successfully")
                    return {
                        "success": True,
                        "message": "Route resynchronization completed",
                        "route_info": route_info,
                    }
                else:
                    return {"success": False, "error": "Route resynchronization failed"}
            except Exception as e:
                logger.error(
                    f"Error handling force_route_resync: {str(e)}", exc_info=True
                )
                return {"success": False, "error": str(e)}

        # Dynamic template control handlers
        self.register_dynamic_control_handlers()

        # Standalone template relay handlers
        self.register_standalone_template_relay_handlers()

        # Chat-specific handlers
        @self.socketio.on("get-streamer-info")
        def handle_get_streamer_info():
            """
            Handle get-streamer-info websocket event.
            Returns the current streamer information for chat templates.
            """
            client_sid = request.sid
            logger.debug(f"Received get-streamer-info request from {client_sid}")
            try:
                from .dataobjects import state_manager

                # Get current Twitch data from state manager
                twitch_data = state_manager.get_twitch_data()
                app_settings = state_manager.get_app_settings()

                if twitch_data and twitch_data.user_id:
                    streamer_info = {
                        "user_id": twitch_data.user_id,
                        "streamer_name": (
                            app_settings.streamer_name
                            if app_settings
                            else "Unknown Streamer"
                        ),
                        "current_category": twitch_data.current_category
                        or "No Category",
                    }
                    self.socketio.emit("streamer-info", streamer_info, to=client_sid)
                    logger.debug(
                        f"Sent streamer info to {client_sid}: {streamer_info['user_id']}"
                    )
                else:
                    # Send default/empty data if no streamer data is available
                    default_info = {
                        "user_id": None,
                        "streamer_name": (
                            app_settings.streamer_name
                            if app_settings
                            else "Unknown Streamer"
                        ),
                        "current_category": "No Category",
                    }
                    self.socketio.emit("streamer-info", default_info, to=client_sid)
                    logger.debug(f"Sent default streamer info to {client_sid}")
                return True
            except Exception as e:
                logger.error(
                    f"Error handling get-streamer-info: {str(e)}", exc_info=True
                )
                self.socketio.emit(
                    "streamer-info-error", {"error": str(e)}, to=client_sid
                )
                return False

        @self.socketio.on("twitch-api-request")
        def handle_twitch_api_request(data):
            """
            Handle twitch-api-request websocket event.
            Proxies Twitch API requests for chat templates (emotes, badges, etc.).

            Args:
                data (dict): Dictionary containing:
                    - endpoint (str): The Twitch API endpoint URL
                    - method (str, optional): HTTP method (default: GET)
                    - requestId (str): Unique request identifier for response matching
            """
            client_sid = request.sid
            logger.debug(f"Received twitch-api-request from {client_sid}: {data}")
            try:
                if (
                    not isinstance(data, dict)
                    or "endpoint" not in data
                    or "requestId" not in data
                ):
                    logger.error("Invalid data format for twitch-api-request")
                    response_data = {
                        "success": False,
                        "error": "Invalid data format: endpoint and requestId required",
                        "requestId": data.get("requestId", "unknown"),
                    }
                    self.socketio.emit(
                        "twitch-api-response", response_data, to=client_sid
                    )
                    return

                endpoint = data["endpoint"]
                method = data.get("method", "GET").upper()
                request_id = data["requestId"]

                # Check if Twitch API is available
                if not twitch.twitch_api:
                    logger.warning("Twitch API not initialized for API request")
                    response_data = {
                        "success": False,
                        "error": "Twitch API not initialized",
                        "requestId": request_id,
                    }
                    self.socketio.emit(
                        "twitch-api-response", response_data, to=client_sid
                    )
                    return

                # Get current Twitch authentication data from state manager
                from .dataobjects import state_manager

                twitch_data = state_manager.get_twitch_data()

                # Check if we have valid authentication credentials from state manager
                if (
                    not twitch_data
                    or not twitch_data.auth_token
                    or not twitch_data.client_id
                ):
                    logger.warning(
                        "Twitch API not authenticated - no valid tokens in state manager"
                    )
                    response_data = {
                        "success": False,
                        "error": "Twitch authentication required",
                        "requestId": request_id,
                    }
                    self.socketio.emit(
                        "twitch-api-response", response_data, to=client_sid
                    )
                    return

                # Update the twitch API instance with current state manager data if needed (thread-safe)
                with self._auth_lock:
                    if (
                        twitch.twitch_api.auth_token != twitch_data.auth_token
                        or twitch.twitch_api.client_id != twitch_data.client_id
                    ):
                        logger.debug(
                            "Updating Twitch API instance with current state manager data"
                        )
                        twitch.twitch_api.auth_token = twitch_data.auth_token
                        twitch.twitch_api.client_id = twitch_data.client_id
                        twitch.twitch_api.refresh_token = twitch_data.refresh_token

                # Run the async handler in a new thread with its own event loop
                def run_async_handler():
                    try:
                        # Create a new event loop for this thread
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                        async def handle_request():
                            try:
                                # Call the generic_api_call method
                                api_response = await twitch.twitch_api.generic_api_call(
                                    url=endpoint, method=method
                                )

                                response_data = {
                                    "success": True,
                                    "data": api_response,
                                    "requestId": request_id,
                                }
                                self.socketio.emit(
                                    "twitch-api-response", response_data, to=client_sid
                                )
                                logger.debug(
                                    f"Twitch API request successful for {endpoint}"
                                )

                            except Exception as e:
                                logger.error(
                                    f"Error in async twitch-api-request handler: {str(e)}",
                                    exc_info=True,
                                )
                                response_data = {
                                    "success": False,
                                    "error": str(e),
                                    "requestId": request_id,
                                }
                                self.socketio.emit(
                                    "twitch-api-response", response_data, to=client_sid
                                )

                        # Run the async function
                        loop.run_until_complete(handle_request())

                    except Exception as e:
                        logger.error(
                            f"Error in thread for twitch-api-request: {str(e)}",
                            exc_info=True,
                        )
                        response_data = {
                            "success": False,
                            "error": str(e),
                            "requestId": request_id,
                        }
                        self.socketio.emit(
                            "twitch-api-response", response_data, to=client_sid
                        )
                    finally:
                        # Clean up the event loop
                        try:
                            loop.close()
                        except:
                            pass

                # Start the thread
                import threading

                thread = threading.Thread(target=run_async_handler, daemon=True)
                thread.start()

            except Exception as e:
                logger.error(
                    f"Error handling twitch-api-request: {str(e)}", exc_info=True
                )
                response_data = {
                    "success": False,
                    "error": str(e),
                    "requestId": request_id,
                }
                self.socketio.emit("twitch-api-response", response_data, to=client_sid)

        @self.socketio.on("debug_psn_data")
        def handle_debug_psn_data(data=None):
            """
            Handle debug_psn_data websocket event for testing PSN data flow.
            """
            client_sid = request.sid
            logger.debug(
                f"=== WEB ENGINE: Debug PSN data request from {client_sid} ==="
            )
            try:
                # Get PSN data from state manager
                live_psn_data = state_manager.get_live_psn_data()

                debug_info = {
                    "timestamp": datetime.now().isoformat(),
                    "client_sid": client_sid,
                    "psn_data_available": live_psn_data is not None,
                    "psn_data_type": str(type(live_psn_data)),
                    "psn_data_fields": (
                        list(dataclasses.asdict(live_psn_data).keys())
                        if live_psn_data
                        else []
                    ),
                    "psn_data_content": (
                        dataclasses.asdict(live_psn_data) if live_psn_data else None
                    ),
                }

                # Send debug info back to client
                self.socketio.emit("debug_psn_response", debug_info, to=client_sid)
                logger.debug(f"Sent debug PSN response to {client_sid}")

                return debug_info
            except Exception as e:
                logger.error(f"Error in debug_psn_data: {str(e)}", exc_info=True)
                error_info = {
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                    "client_sid": client_sid,
                }
                self.socketio.emit("debug_psn_response", error_info, to=client_sid)
                return error_info

        @self.socketio.on("submit_template_stat")
        def handle_submit_template_stat(data):
            """
            Handle submit_template_stat websocket event.
            Allows HTML templates to submit custom statistics.

            Args:
                data (dict): Dictionary containing:
                    - template_name (str): Name of the template submitting the stat
                    - stat_name (str): Name of the statistic
                    - stat_value (any): Value of the statistic
                    - increment (bool, optional): Whether to increment existing value (default: False)
            """
            client_sid = request.sid
            logger.debug(f"Received submit_template_stat from {client_sid}: {data}")

            try:
                if not isinstance(data, dict):
                    logger.error(
                        "Invalid data format for submit_template_stat: must be a dictionary"
                    )
                    self.socketio.emit(
                        "template_stat_response",
                        {
                            "success": False,
                            "error": "Invalid data format: must be a dictionary",
                        },
                        to=client_sid,
                    )
                    return

                # Validate required fields
                required_fields = ["template_name", "stat_name", "stat_value"]
                missing_fields = [
                    field for field in required_fields if field not in data
                ]

                if missing_fields:
                    logger.error(
                        f"Missing required fields in submit_template_stat: {missing_fields}"
                    )
                    self.socketio.emit(
                        "template_stat_response",
                        {
                            "success": False,
                            "error": f"Missing required fields: {', '.join(missing_fields)}",
                        },
                        to=client_sid,
                    )
                    return

                template_name = data["template_name"]
                stat_name = data["stat_name"]
                stat_value = data["stat_value"]
                increment = data.get("increment", False)

                # Get the statistics manager and submit the stat
                stats_manager = statistics_manager.get_statistics_manager()
                success = stats_manager.submit_template_stat(
                    template_name=template_name,
                    stat_name=stat_name,
                    stat_value=stat_value,
                    increment=increment,
                )

                if success:
                    response = {
                        "success": True,
                        "template_name": template_name,
                        "stat_name": stat_name,
                        "stat_value": stat_value,
                        "increment": increment,
                    }
                    logger.debug(
                        f"Successfully submitted template stat: {template_name}.{stat_name} = {stat_value}"
                    )
                else:
                    response = {
                        "success": False,
                        "error": "Failed to submit template statistic",
                        "template_name": template_name,
                        "stat_name": stat_name,
                    }
                    logger.error(
                        f"Failed to submit template stat: {template_name}.{stat_name}"
                    )

                # Send response back to client
                self.socketio.emit("template_stat_response", response, to=client_sid)

            except Exception as e:
                logger.error(
                    f"Error handling submit_template_stat: {str(e)}", exc_info=True
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                }
                self.socketio.emit(
                    "template_stat_response", error_response, to=client_sid
                )

        @self.socketio.on("get_template_configs_for_streamdeck")
        def handle_get_template_configs_for_streamdeck(data=None):
            """
            Handle get_template_configs_for_streamdeck websocket event.
            Returns template configurations for Stream Deck integration.

            Args:
                data (dict, optional): Optional parameters for filtering
            """
            client_sid = request.sid
            logger.debug(
                f"Received get_template_configs_for_streamdeck from {client_sid}"
            )

            try:
                # Get template configurations with Stream Deck options
                streamdeck_configs = (
                    self.template_config_parser.get_streamdeck_configs()
                )

                # Also include templates with dynamic controls as fallback
                dynamic_configs = {}
                all_configs = self.template_config_parser.get_config_files()

                for config_name in all_configs:
                    if config_name not in streamdeck_configs:
                        try:
                            config = self.template_config_parser.load_config(
                                config_name, include_dynamic_controls=True
                            )
                            if (
                                isinstance(config, dict)
                                and "dynamic_controls" in config
                            ):
                                dynamic_configs[config_name] = config
                                logger.debug(
                                    f"Included template with dynamic controls: {config_name}"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Error loading dynamic controls for {config_name}: {str(e)}"
                            )

                # Merge both types of configs
                all_template_configs = {**streamdeck_configs, **dynamic_configs}

                response = {
                    "event": "template_configs_response",
                    "data": all_template_configs,
                    "count": len(all_template_configs),
                }

                self.socketio.emit("template_configs_response", response, to=client_sid)
                logger.debug(
                    f"Sent {len(all_template_configs)} template configs to Stream Deck client"
                )

            except Exception as e:
                logger.error(
                    f"Error handling get_template_configs_for_streamdeck: {str(e)}",
                    exc_info=True,
                )
                error_response = {
                    "event": "template_configs_response",
                    "error": str(e),
                    "data": {},
                    "count": 0,
                }
                self.socketio.emit(
                    "template_configs_response", error_response, to=client_sid
                )

        @self.socketio.on("streamdeck_template_action")
        def handle_streamdeck_template_action(data):
            """
            Handle streamdeck_template_action websocket event.
            Executes a template action triggered from Stream Deck.

            Args:
                data (dict): Dictionary containing:
                    - templateName (str): Name of the template
                    - actionName (str): Name of the action to execute
                    - actionData (dict, optional): Additional data for the action
            """
            client_sid = request.sid
            logger.debug(
                f"Received streamdeck_template_action from {client_sid}: {data}"
            )

            try:
                if not isinstance(data, dict):
                    logger.error("Invalid data format for streamdeck_template_action")
                    self.socketio.emit(
                        "streamdeck_action_response",
                        {"success": False, "error": "Invalid data format"},
                        to=client_sid,
                    )
                    return

                template_name = data.get("templateName")
                action_name = data.get("actionName")
                action_data = data.get("actionData", {})

                if not template_name or not action_name:
                    logger.error(
                        "Missing templateName or actionName in streamdeck_template_action"
                    )
                    self.socketio.emit(
                        "streamdeck_action_response",
                        {
                            "success": False,
                            "error": "Missing templateName or actionName",
                        },
                        to=client_sid,
                    )
                    return

                # Load template configuration
                template_config = self.template_config_parser.load_config(
                    template_name,
                    include_dynamic_controls=True,
                    include_streamdeck_options=True,
                )

                if not template_config:
                    logger.error(f"Template configuration not found: {template_name}")
                    self.socketio.emit(
                        "streamdeck_action_response",
                        {
                            "success": False,
                            "error": f"Template not found: {template_name}",
                        },
                        to=client_sid,
                    )
                    return

                # Try to find the action in streamdeck_options first
                action_found = False
                if (
                    "streamdeck_options" in template_config
                    and "actions" in template_config["streamdeck_options"]
                ):
                    streamdeck_actions = template_config["streamdeck_options"][
                        "actions"
                    ]
                    if action_name in streamdeck_actions:
                        action_config = streamdeck_actions[action_name]
                        action_found = True
                        logger.debug(
                            f"Found Stream Deck action: {template_name}.{action_name}"
                        )

                        # Execute the Stream Deck specific action
                        self._execute_streamdeck_action(
                            template_name, action_name, action_config, action_data
                        )

                # Fallback to dynamic_controls or connector_actions
                if not action_found:
                    if (
                        "dynamic_controls" in template_config
                        and "elements" in template_config["dynamic_controls"]
                    ):
                        elements = template_config["dynamic_controls"]["elements"]
                        for element in elements:
                            if isinstance(element, dict):
                                element_action = element.get("action")
                                if element_action == action_name:
                                    action_found = True
                                    logger.debug(
                                        f"Found dynamic control action: {template_name}.{action_name}"
                                    )
                                    self._execute_dynamic_control_action(
                                        template_name, element, action_data
                                    )
                                    break

                    elif "connector_actions" in template_config:
                        connector_actions = template_config["connector_actions"]
                        if action_name in connector_actions:
                            action_found = True
                            logger.debug(
                                f"Found connector action: {template_name}.{action_name}"
                            )
                            self._execute_connector_action(
                                template_name,
                                action_name,
                                connector_actions[action_name],
                                action_data,
                            )

                if not action_found:
                    logger.error(f"Action not found: {template_name}.{action_name}")
                    self.socketio.emit(
                        "streamdeck_action_response",
                        {"success": False, "error": f"Action not found: {action_name}"},
                        to=client_sid,
                    )
                    return

                # Send success response
                response = {
                    "success": True,
                    "templateName": template_name,
                    "actionName": action_name,
                    "message": f"Executed {template_name}.{action_name}",
                }
                self.socketio.emit(
                    "streamdeck_action_response", response, to=client_sid
                )
                logger.info(
                    f"Stream Deck action executed: {template_name}.{action_name}"
                )

            except Exception as e:
                logger.error(
                    f"Error handling streamdeck_template_action: {str(e)}",
                    exc_info=True,
                )
                error_response = {"success": False, "error": str(e)}
                self.socketio.emit(
                    "streamdeck_action_response", error_response, to=client_sid
                )

    def _execute_streamdeck_action(
        self,
        template_name: str,
        action_name: str,
        action_config: dict,
        action_data: dict,
    ):
        """Execute a Stream Deck specific action"""
        try:
            # Check if there's a specific event to emit
            event_name = action_config.get("event", f"{template_name}_{action_name}")
            event_data = action_config.get("default_data", {})

            # Merge with provided action data
            if action_data:
                event_data.update(action_data)

            # Emit the event to all clients
            self.socketio.emit(event_name, event_data)
            logger.debug(
                f"Emitted Stream Deck event: {event_name} with data: {event_data}"
            )

        except Exception as e:
            logger.error(
                f"Error executing Stream Deck action {template_name}.{action_name}: {str(e)}",
                exc_info=True,
            )

    def _execute_dynamic_control_action(
        self, template_name: str, element_config: dict, action_data: dict
    ):
        """Execute a dynamic control action"""
        try:
            action = element_config.get("action", "")

            # For counter controls, handle specific actions
            if element_config.get("type") == "counter_control":
                if action.endswith("_increment") or action == "increment":
                    self.socketio.emit("counter_update", {"action": "increment"})
                elif action.endswith("_decrement") or action == "decrement":
                    self.socketio.emit("counter_update", {"action": "decrement"})
                elif action.endswith("_reset") or action == "reset":
                    self.socketio.emit("counter_update", {"action": "reset"})
            else:
                # Generic action emission
                event_name = f"{template_name}_{action}"
                self.socketio.emit(event_name, action_data or {})
                logger.debug(f"Emitted dynamic control event: {event_name}")

        except Exception as e:
            logger.error(
                f"Error executing dynamic control action for {template_name}: {str(e)}",
                exc_info=True,
            )

    def _execute_connector_action(
        self,
        template_name: str,
        action_name: str,
        action_config: dict,
        action_data: dict,
    ):
        """Execute a connector action"""
        try:
            trigger = action_config.get("trigger", action_name)

            # Create the event data
            event_data = {
                "action": trigger,
                "template": template_name,
                "data": action_data,
            }

            # Emit the connector event
            self.socketio.emit(f"connector_{trigger}", event_data)
            logger.debug(f"Emitted connector event: connector_{trigger}")

        except Exception as e:
            logger.error(
                f"Error executing connector action {template_name}.{action_name}: {str(e)}",
                exc_info=True,
            )

    def toggle_alerts(self):
        global ALERTS_PAUSED
        old_status = ALERTS_PAUSED
        ALERTS_PAUSED = not ALERTS_PAUSED  # Toggle the status

        logger.info(f"Toggling ALERTS_PAUSED from {old_status} to {ALERTS_PAUSED}")
        print(f"ALERTS_PAUSED toggled: {old_status} -> {ALERTS_PAUSED}")

        # Broadcast to all connected clients to ensure synchronization
        self.pause_status_update()

    def register_dynamic_control_handlers(self):
        """Register websocket handlers for dynamic template controls"""
        # This method now calls register_additional_control_handlers to do the actual work
        self.register_additional_control_handlers()

    def register_additional_control_handlers(self):
        """Register additional websocket handlers for any new dynamic controls"""
        try:
            # Skip if template_config_parser is not available to avoid import delays
            try:
                from . import template_config_parser
            except ImportError:
                logger.debug(
                    "Template config parser not available, skipping dynamic control handlers"
                )
                return

            # Initialize template config parser with timeout protection
            try:
                config_parser = template_config_parser.TemplateConfigParser()
                config_files = config_parser.get_config_files()
            except Exception as e:
                logger.warning(f"Could not load template config files: {str(e)}")
                return

            # Skip if no config files to avoid unnecessary processing
            if not config_files:
                logger.debug(
                    "No template config files found, skipping dynamic control handlers"
                )
                return

            # Keep track of registered handlers to avoid duplicates
            if not hasattr(self, "_registered_dynamic_handlers"):
                self._registered_dynamic_handlers = set()

            logger.debug(
                f"Scanning {len(config_files)} config files for dynamic controls"
            )

            # Limit the number of config files processed to avoid startup delays
            max_configs = 50  # Process at most 50 config files during startup
            processed_count = 0

            for config_name in config_files:
                if processed_count >= max_configs:
                    logger.debug(
                        f"Reached maximum config file limit ({max_configs}), skipping remaining files"
                    )
                    break

                try:
                    config = config_parser.load_config(
                        config_name, include_dynamic_controls=True
                    )

                    if isinstance(config, dict) and "dynamic_controls" in config:
                        dynamic_controls = config["dynamic_controls"]
                        elements = dynamic_controls.get("elements", [])

                        logger.debug(
                            f"Found {len(elements)} dynamic control elements in {config_name}"
                        )

                        for element in elements:
                            if isinstance(element, dict):
                                self._register_element_handler(config_name, element)

                    processed_count += 1

                except Exception as e:
                    logger.warning(
                        f"Error registering handlers for {config_name}: {str(e)}"
                    )
                    # Don't log full traceback to avoid spam during startup

        except Exception as e:
            logger.warning(f"Error in register_additional_control_handlers: {str(e)}")
            # Don't log full traceback to avoid spam during startup

    def _register_element_handler(self, template_name, element):
        """Register a websocket handler for a specific element"""
        try:
            element_type = element.get("type", "")
            element_id = element.get("id", "")

            # Handle different types of controls and their actions
            actions_to_register = []

            if element_type == "counter_control":
                # Counter controls have multiple actions
                actions_to_register.extend(
                    [
                        element.get("action_increment", "counter_increment"),
                        element.get("action_decrement", "counter_decrement"),
                        element.get("action_reset", "counter_reset"),
                    ]
                )
            elif element_type in [
                "button",
                "spin_control",
                "toggle",
                "text_input",
                "number_input",
                "slider",
            ]:
                # Single action controls
                action = element.get("action", "")
                if action:
                    actions_to_register.append(action)

            # Register handlers for each action
            for action in actions_to_register:
                if not action:
                    continue

                event_name = f"{template_name}_{action}"

                # Check if handler already registered
                if event_name in self._registered_dynamic_handlers:
                    continue

                # Create and register the handler
                handler_func = self._create_dynamic_handler(
                    template_name, action, element_type, element
                )
                self.socketio.on(event_name)(handler_func)
                self._registered_dynamic_handlers.add(event_name)
                logger.debug(
                    f"Registered dynamic handler for {event_name} (type: {element_type})"
                )

        except Exception as e:
            logger.error(
                f"Error registering handler for {template_name}.{element.get('id', 'unknown')}: {str(e)}",
                exc_info=True,
            )

    def _create_dynamic_handler(
        self, template_name, action, element_type, element_config
    ):
        """Create a dynamic websocket handler function"""

        def handler(data=None):
            logger.debug(
                f"Dynamic control event: {template_name}_{action} (type: {element_type}) with data: {data}"
            )

            try:
                # Handle different control types with specific logic
                if element_type == "counter_control":
                    self._handle_counter_control(template_name, action, data)
                elif element_type == "spin_control":
                    self._handle_spin_control(template_name, action, data)
                elif element_type == "button":
                    self._handle_button_control(
                        template_name, action, data, element_config
                    )
                elif element_type == "text_input":
                    self._handle_text_input_control(template_name, action, data)
                elif element_type == "number_input":
                    self._handle_number_input_control(template_name, action, data)
                elif element_type == "slider":
                    self._handle_slider_control(template_name, action, data)
                elif element_type == "toggle":
                    self._handle_toggle_control(template_name, action, data)
                else:
                    # Generic fallback - just forward the event
                    self.socketio.emit(f"{template_name}_{action}", data or {})

                return True
            except Exception as e:
                logger.error(
                    f"Error handling dynamic control event {template_name}_{action}: {str(e)}",
                    exc_info=True,
                )
                return False

        return handler

    def _handle_counter_control(self, template_name, action, data):
        """Handle counter control actions"""
        if template_name == "counter":
            if (
                action.endswith("_increment")
                or action == "counter_increment"
                or action == "increment"
            ):
                self.socketio.emit("counter_update", {"action": "increment"})
            elif (
                action.endswith("_decrement")
                or action == "counter_decrement"
                or action == "decrement"
            ):
                self.socketio.emit("counter_update", {"action": "decrement"})
            elif (
                action.endswith("_reset")
                or action == "counter_reset"
                or action == "reset"
            ):
                self.socketio.emit("counter_update", {"action": "reset"})
            elif action == "counter_message":
                if data and "text" in data:
                    self.socketio.emit("counter_message", {"message": data["text"]})
            elif action == "counter_update":
                if data and "value" in data:
                    self.socketio.emit(
                        "counter_update", {"action": "set", "value": data["value"]}
                    )
        elif action in ("increment", "decrement", "reset"):
            self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_spin_control(self, template_name, action, data):
        """Handle spin control actions"""
        if action == "spin":
            self.socketio.emit("spin-wheel", data or {})
        else:
            self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_button_control(self, template_name, action, data, element_config):
        """Handle button control actions"""
        # Check for specific template actions
        # if template_name == 'roulette':
        #     if action == 'reset':
        #         self.socketio.emit('roulette_update', {'action': 'reset'})
        #     elif action == 'clear_options':
        #         self.socketio.emit('roulette_update', {'action': 'clear_options'})
        #     else:
        #         self.socketio.emit(f"roulette_{action}", data or {})
        # elif template_name == 'ttimer':
        #     if action in ['reset', 'start', 'pause']:
        #         self.socketio.emit(f"timer_{action}", data or {})
        #     else:
        #         self.socketio.emit(f"ttimer_{action}", data or {})
        # elif template_name == 'memecalc':
        #     if action == 'reset':
        #         self.socketio.emit('memecalc_reset', data or {})
        #     else:
        #         self.socketio.emit(f"memecalc_{action}", data or {})
        # elif template_name == 'combobar':
        #     # Fix double prefix issue: actions already contain template prefix
        #     if action == 'reset_combobar':
        #         self.socketio.emit('combobar_reset', data or {})
        #     elif action == 'test_levelup':
        #         self.socketio.emit('combobar_test_levelup', data or {})
        #     elif action == 'add_test_exp':
        #         self.socketio.emit('combobar_add_exp', data or {})
        #     elif action == 'toggle_bonus':
        #         self.socketio.emit('combobar_bonus_toggle', data or {})
        #     elif action.startswith('combobar_'):
        #         # Actions already contain the template prefix, emit as-is
        #         self.socketio.emit(action, data or {})
        #     else:
        #         # Fallback for actions without prefix
        #         self.socketio.emit(f"combobar_{action}", data or {})
        # else:
        #     # Generic button action
        self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_text_input_control(self, template_name, action, data):
        """Handle text input control actions"""
        if template_name == "roulette" and action == "add_option":
            if data and "text" in data:
                self.socketio.emit(
                    "roulette_update", {"action": "add_option", "option": data["text"]}
                )
        elif template_name == "counter" and action == "counter_message":
            if data and "text" in data:
                self.socketio.emit("counter_message", {"message": data["text"]})
        else:
            # Generic text input
            self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_number_input_control(self, template_name, action, data):
        """Handle number input control actions"""
        if template_name == "counter" and action == "counter_update":
            if data and "value" in data:
                self.socketio.emit(
                    "counter_update", {"action": "set", "value": data["value"]}
                )
        elif template_name == "ttimer" and action == "set":
            if data and "value" in data:
                self.socketio.emit("timer_set", {"value": data["value"]})
        elif template_name == "memecalc":
            if action == "update" and data and "value" in data:
                self.socketio.emit(
                    "memecalc_update", {"action": "set", "value": data["value"]}
                )
            elif action == "add_bits" and data and "value" in data:
                self.socketio.emit(
                    "memecalc_update", {"action": "add", "value": data["value"]}
                )
            else:
                self.socketio.emit(f"memecalc_{action}", data or {})
        elif template_name == "combobar":
            # Fix double prefix issue: actions already contain template prefix
            if action.startswith("combobar_"):
                # Actions already contain the template prefix, emit as-is
                self.socketio.emit(action, data or {})
            else:
                # Fallback for actions without prefix
                self.socketio.emit(f"combobar_{action}", data or {})
        else:
            # Generic number input
            self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_slider_control(self, template_name, action, data):
        """Handle slider control actions"""
        self.socketio.emit(f"{template_name}_{action}", data or {})

    def _handle_toggle_control(self, template_name, action, data):
        """Handle toggle control actions"""
        if template_name == "combobar":
            # Fix double prefix issue: actions already contain template prefix
            if action.startswith("combobar_"):
                # Actions already contain the template prefix, emit as-is
                self.socketio.emit(action, data or {})
            else:
                # Fallback for actions without prefix
                self.socketio.emit(f"combobar_{action}", data or {})
        else:
            self.socketio.emit(f"{template_name}_{action}", data or {})

    def persist_template_control_change(
        self, template_name: str, action: str, data: Optional[Dict[str, Any]]
    ) -> None:
        """
        Persist template config JSON when source controls change durable values.

        In each template's ``dynamic_controls.elements`` entry, optional::

            "persist": {
                "target_element_id": "CurrentSubs",
                "value_key": "value",
                "value_type": "int_nonneg",
                "sync_dynamic_value": true,
                "runtime_database_path": "MyTemplate/state",
                "runtime_state_key": "current"
            }

        ``value_key`` defaults to ``value`` (use ``text`` for text inputs, ``enabled`` for toggles).
        ``value_type``: int_nonneg, int_min1, int, float, str, bool, any (no coercion).
        Optional ``runtime_database_path`` + ``runtime_state_key`` load initial Source
        Controls values from ``database_manager.get_data`` when that document contains
        the key (e.g. templates that ``set_data`` live state).
        """
        data = data or {}
        if not template_name or not action:
            return
        try:
            parser = self.template_config_parser
            config = parser.load_config(template_name, include_dynamic_controls=True)
            ctrl = self._dynamic_control_element_for_action(config, action)
            if not ctrl:
                return
            persist = ctrl.get("persist")
            if not isinstance(persist, dict):
                return
            target_id = persist.get("target_element_id")
            if not target_id:
                return
            value_key = persist.get("value_key", "value")
            if value_key not in data:
                return
            raw = data[value_key]
            if raw is None:
                return
            value_type = persist.get("value_type", "int")
            coerced = self._coerce_persisted_control_value(raw, value_type)
            main_elements = config.get("elements", [])
            updated = False
            for el in main_elements:
                if isinstance(el, dict) and el.get("id") == target_id:
                    el["value"] = coerced
                    updated = True
                    break
            if not updated:
                logger.warning(
                    "Persist: no template element with id %r in %s",
                    target_id,
                    template_name,
                )
                return
            if persist.get("sync_dynamic_value", True):
                ctrl["value"] = coerced
            parser.save_config(template_name, config)
            self._emit_source_controls_state_update(
                reason="persisted_control_change",
                template_names=[template_name],
                action=action,
                data=data,
            )
            logger.debug(
                "Persisted template %s action=%s -> elements[%r]",
                template_name,
                action,
                target_id,
            )
        except Exception as e:
            logger.error(
                "Error persisting template control %s %s: %s",
                template_name,
                action,
                e,
                exc_info=True,
            )

    def _source_control_templates_for_runtime_path(self, db_path: str) -> List[str]:
        """Return template names whose dynamic controls mirror the given runtime DB path."""
        if not db_path or not isinstance(db_path, str):
            return []
        try:
            matches = []
            for config_name in self.template_config_parser.get_config_files():
                config = self.template_config_parser.load_config(
                    config_name, include_dynamic_controls=True
                )
                dynamic_controls = config.get("dynamic_controls") or {}
                for element in dynamic_controls.get("elements", []):
                    if not isinstance(element, dict):
                        continue
                    persist = element.get("persist")
                    if not isinstance(persist, dict):
                        continue
                    if persist.get("runtime_database_path") == db_path:
                        matches.append(config_name)
                        break
            return matches
        except Exception as e:
            logger.debug(
                "Unable to resolve source-control templates for %s: %s", db_path, e
            )
            return []

    def _emit_source_controls_state_update(
        self,
        *,
        reason: str,
        template_names: Optional[List[str]] = None,
        db_path: Optional[str] = None,
        action: Optional[str] = None,
        event_name: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Broadcast a lightweight signal so source-control UIs can reload live values."""
        payload = {
            "reason": reason,
            "template_names": template_names or [],
            "db_path": db_path,
            "action": action,
            "event_name": event_name,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        self.socketio.emit("source_controls_state_update", payload)
        try:
            from .uiwindows import sourcecontrols

            sourcecontrols.mark_source_controls_dirty(payload)
        except Exception as e:
            logger.debug("Unable to notify NiceGUI source controls: %s", e)

    def _broadcast_source_control_runtime_path_update(
        self, db_path: str, state: Optional[Dict[str, Any]]
    ) -> None:
        """Broadcast runtime DB writes that affect source-control values."""
        template_names = self._source_control_templates_for_runtime_path(db_path)
        if not template_names:
            return
        self._emit_source_controls_state_update(
            reason="runtime_db_update",
            template_names=template_names,
            db_path=db_path,
            data=state if isinstance(state, dict) else {},
        )

    @staticmethod
    def _dynamic_control_element_for_action(
        config: Dict[str, Any], action: str
    ) -> Optional[Dict[str, Any]]:
        """Return the dynamic_controls element whose action (or counter action) matches."""
        dc = config.get("dynamic_controls") or {}
        for el in dc.get("elements", []):
            if not isinstance(el, dict):
                continue
            candidates = []
            if el.get("action"):
                candidates.append(el["action"])
            for k in ("action_increment", "action_decrement", "action_reset"):
                if el.get(k):
                    candidates.append(el[k])
            if action in candidates:
                return el
        return None

    @staticmethod
    def _coerce_persisted_control_value(raw: Any, value_type: str) -> Any:
        vt = (value_type or "str").strip().lower()
        if vt in ("any", "raw", "json"):
            return raw
        if vt in ("int_nonneg", "uint", "nonneg_int"):
            return max(0, int(float(raw)))
        if vt in ("int_min1", "positive_int"):
            return max(1, int(float(raw)))
        if vt in ("int", "integer"):
            return int(float(raw))
        if vt in ("float", "number", "double"):
            return float(raw)
        if vt in ("bool", "boolean"):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        if vt in ("str", "string", "text"):
            return str(raw)
        logger.warning("Unknown persist value_type %r, storing as string", value_type)
        return str(raw)

    def register_standalone_template_relay_handlers(self):
        """Register a generic WebSocket relay handler for standalone source_controls template"""
        try:

            @self.socketio.on("source_controls_relay")
            def handle_source_controls_relay(data):
                """
                Generic relay handler for source controls events

                Args:
                    data (dict): Dictionary containing:
                        - event_name (str): The WebSocket event name to emit
                        - target_template (str): The target template name (for logging)
                        - event_data (dict): The data to send with the event
                """
                try:
                    if not isinstance(data, dict):
                        logger.error(
                            "Invalid data format for source_controls_relay: must be a dictionary"
                        )
                        return {"success": False, "error": "Invalid data format"}

                    event_name = data.get("event_name")
                    target_template = data.get("target_template", "unknown")
                    event_data = data.get("event_data", {})

                    if not event_name:
                        logger.error("Missing event_name in source_controls_relay")
                        return {"success": False, "error": "Missing event_name"}

                    # Log the relay
                    logger.debug(
                        f"Source controls relay: {event_name} -> {target_template} with data: {event_data}"
                    )

                    # Emit the event to all connected clients
                    self.socketio.emit(event_name, event_data)
                    self._emit_source_controls_state_update(
                        reason="relay_event",
                        template_names=[target_template] if target_template else [],
                        action=event_name.split("_", 1)[1]
                        if "_" in event_name
                        else None,
                        event_name=event_name,
                        data=event_data if isinstance(event_data, dict) else {},
                    )

                    return {
                        "success": True,
                        "event_name": event_name,
                        "target_template": target_template,
                    }

                except Exception as e:
                    logger.error(
                        f"Error in source_controls_relay handler: {str(e)}",
                        exc_info=True,
                    )
                    return {"success": False, "error": str(e)}

            logger.debug("Registered generic source controls relay handler")

        except Exception as e:
            logger.error(
                f"Error registering source controls relay handler: {str(e)}",
                exc_info=True,
            )

    def get_registered_dynamic_handlers(self):
        """Get a list of all registered dynamic control handlers"""
        if hasattr(self, "_registered_dynamic_handlers"):
            return sorted(list(self._registered_dynamic_handlers))
        return []

    def clear_dynamic_handlers(self):
        """Clear all registered dynamic handlers (useful for reloading)"""
        if hasattr(self, "_registered_dynamic_handlers"):
            self._registered_dynamic_handlers.clear()
            logger.debug("Cleared all registered dynamic handlers")

    def get_registered_template_routes(self):
        """Get detailed information about registered template routes"""
        try:
            routes_info = {
                "template_routes": sorted(list(self._registered_template_routes)),
                "total_routes": len(self._registered_template_routes),
                "flask_rules": [],
                "template_files_on_disk": [],
            }

            # Get Flask URL rules for template routes
            for rule in self.app.url_map.iter_rules():
                if rule.endpoint.startswith("route_"):
                    routes_info["flask_rules"].append(
                        {
                            "rule": str(rule.rule),
                            "endpoint": rule.endpoint,
                            "methods": sorted(rule.methods),
                        }
                    )

            # Get actual template files on disk
            if os.path.exists(self.template_dir):
                html_files = glob.glob(os.path.join(self.template_dir, "*.html"))
                routes_info["template_files_on_disk"] = sorted(
                    [os.path.basename(f).replace(".html", "") for f in html_files]
                )

            # Check for discrepancies
            registered_set = set(routes_info["template_routes"])
            on_disk_set = set(routes_info["template_files_on_disk"])

            routes_info["discrepancies"] = {
                "registered_but_missing_file": sorted(registered_set - on_disk_set),
                "file_exists_but_not_registered": sorted(on_disk_set - registered_set),
            }

            return routes_info

        except Exception as e:
            logger.error(
                f"Error getting registered template routes: {str(e)}", exc_info=True
            )
            return {
                "error": str(e),
                "template_routes": [],
                "total_routes": 0,
                "flask_rules": [],
                "template_files_on_disk": [],
            }

    def force_route_resync(self):
        """Force a complete resynchronization of template routes"""
        try:
            logger.info("Forcing complete route resynchronization...")

            # Clear tracking
            self._registered_template_routes.clear()

            # Rebuild URL map
            self._rebuild_url_map()

            logger.info("Route resynchronization completed")
            return True

        except Exception as e:
            logger.error(
                f"Error during route resynchronization: {str(e)}", exc_info=True
            )
            return False

    def next_alert(self, alert_data):
        """
        Trigger a 'next_alert' websocket event

        Args:
            alert_data (dict): Alert data to send as JSON
        """
        try:
            self.socketio.emit("next_alert", alert_data)
            logger.debug(f"Sent next_alert: {alert_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending next_alert: {str(e)}", exc_info=True)
            return False

    def activity_feed_alert(self, alert_data):
        """
        Trigger an 'activity_feed_alert' websocket event specifically for the activity feed

        Args:
            alert_data (dict): Alert data to send as JSON to activity feed
        """
        try:
            self.socketio.emit("activity_feed_alert", alert_data)
            logger.debug(f"Sent activity_feed_alert: {alert_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending activity_feed_alert: {str(e)}", exc_info=True)
            return False

    def instant_alert(self, alert_data):
        """
        Trigger an 'instant_alert' websocket event for immediate alert notifications

        Args:
            alert_data (dict): Alert data to send as JSON for instant alerts
        """
        try:
            self.socketio.emit("instant_alert", alert_data)
            logger.debug(f"Sent instant_alert: {alert_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending instant_alert: {str(e)}", exc_info=True)
            return False

    def new_message(self, message_data):
        """
        Trigger a 'new_message' websocket event

        Args:
            message_data (dict): Message data to send as JSON
        """
        try:
            self.socketio.emit("new-message", message_data)
            logger.debug(f"Sent new-message: {message_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending new-message: {str(e)}", exc_info=True)
            return False

    def message_moderation(self, moderation_data):
        """
        Trigger a 'message_moderation' websocket event

        Args:
            moderation_data (dict): Moderation data to send as JSON
        """
        try:
            self.socketio.emit("message_moderation", moderation_data)
            logger.debug(f"Sent message_moderation: {moderation_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending message_moderation: {str(e)}", exc_info=True)
            return False

    def state(self, state_data):
        """
        Trigger a 'state' websocket event

        Args:
            state_data (dict): State data to send as JSON
        """
        try:
            self.socketio.emit("state", state_data)
            logger.debug(f"Sent state update: {state_data}")
            return True
        except Exception as e:
            logger.error(f"Error sending state update: {str(e)}", exc_info=True)
            return False

    def pause_status_update(self):
        """
        Broadcast pause status update to all connected clients
        """
        try:
            global ALERTS_PAUSED

            logger.info(f"Broadcasting pause status update: paused={ALERTS_PAUSED}")

            # Send specific pause/resume events (for backward compatibility)
            if ALERTS_PAUSED:
                self.socketio.emit("alerts_paused", {"paused": True})
                logger.debug("Emitted 'alerts_paused' event")
            else:
                self.socketio.emit("alerts_resumed", {"paused": False})
                logger.debug("Emitted 'alerts_resumed' event")

            # Send general status update (primary event)
            self.socketio.emit("pause_status_update", {"paused": ALERTS_PAUSED})
            logger.debug("Emitted 'pause_status_update' event")

            # Also emit legacy pause_alerts event for any templates that only listen for this
            self.socketio.emit("pause_alerts", {"paused": ALERTS_PAUSED})
            logger.debug("Emitted legacy 'pause_alerts' event")

            logger.info(
                f"Successfully broadcasted pause status update: paused={ALERTS_PAUSED}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Error broadcasting pause status update: {str(e)}", exc_info=True
            )
            return False

    def hype_train_status_update(self, is_active, hype_train_data=None):
        """
        Broadcast hype train status update to all connected clients

        Args:
            is_active (bool): Whether the hype train is currently active
            hype_train_data (dict, optional): Additional hype train data
        """
        try:
            status_data = {"active": is_active, "timestamp": datetime.now().isoformat()}

            # Add additional hype train data if provided
            if hype_train_data:
                status_data.update(hype_train_data)

            self.socketio.emit("hype_train_active", status_data)
            logger.debug(f"Emitted hype_train_active event: active={is_active}")

            return True
        except Exception as e:
            logger.error(
                f"Error broadcasting hype train status update: {str(e)}", exc_info=True
            )
            return False

    def register_streamdeck_websocket_handlers(self):
        """Register WebSocket event handlers for Stream Deck communication"""
        logger.info("Registering Stream Deck WebSocket handlers")

        @self.socketio.on("streamdeck_toggle_alerts")
        def handle_streamdeck_toggle_alerts(data=None):
            """Handle SocketIO event to toggle alert pause/resume status"""
            try:
                global ALERTS_PAUSED
                old_status = ALERTS_PAUSED
                ALERTS_PAUSED = not ALERTS_PAUSED

                logger.info(
                    f"Stream Deck: Alerts paused status changed from {old_status} to {ALERTS_PAUSED}"
                )

                # Emit appropriate events to all connected templates
                if ALERTS_PAUSED:
                    self.socketio.emit("alerts_paused", {"paused": True})
                else:
                    self.socketio.emit("alerts_resumed", {"paused": False})

                # Also emit the general status update
                self.socketio.emit("pause_status_update", {"paused": ALERTS_PAUSED})

                # Send response back to Stream Deck
                response = {
                    "success": True,
                    "paused": ALERTS_PAUSED,
                    "message": f"Alerts {'paused' if ALERTS_PAUSED else 'resumed'}",
                }

                # Handle SocketIO client response format
                if data and isinstance(data, dict) and "requestId" in data:
                    # Send response back to the specific client via the 'response' event
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                    # Also emit connection status update for UI
                    self.socketio.emit(
                        "connection_status_update",
                        {"connected": response.get("connected", True)},
                        to=request.sid,
                    )
                else:
                    # Return for direct handler response
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error toggling alerts: {str(e)}", exc_info=True
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to toggle alerts",
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        @self.socketio.on("streamdeck_get_pause_status")
        def handle_streamdeck_get_pause_status(data=None):
            """Handle SocketIO event to get current alert pause status"""
            try:
                global ALERTS_PAUSED

                response = {
                    "success": True,
                    "paused": ALERTS_PAUSED,
                    "message": f"Alerts are currently {'paused' if ALERTS_PAUSED else 'active'}",
                }

                # Handle SocketIO client response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error getting pause status: {str(e)}", exc_info=True
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to get pause status",
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        @self.socketio.on("streamdeck_template_action")
        def handle_streamdeck_template_action(data=None):
            """Handle SocketIO event to execute template actions"""
            try:
                # Handle SocketIO client message format
                if data and isinstance(data, dict) and "event" in data:
                    # Extract the actual request data from SocketIO client format
                    request_data = data
                    template_name = request_data.get("templateName", "")
                    action_name = request_data.get("actionName", "")
                    event_name = request_data.get("eventName", action_name)
                    action_data = request_data.get("actionData", {})
                else:
                    # Direct handler call (fallback)
                    if not isinstance(data, dict):
                        response = {
                            "success": False,
                            "error": "Invalid data format",
                            "message": "Request must contain JSON data",
                        }
                        if hasattr(request, "sid"):
                            self.socketio.emit(
                                "response",
                                {
                                    **response,
                                    "requestId": data.get("requestId")
                                    if data
                                    else None,
                                },
                                to=request.sid,
                            )
                        return response

                    template_name = data.get("templateName", "")
                    action_name = data.get("actionName", "")
                    event_name = data.get("eventName", action_name)
                    action_data = data.get("actionData", {})

                if not template_name or not action_name:
                    response = {
                        "success": False,
                        "error": "Missing required fields",
                        "message": "templateName and actionName are required",
                    }
                    if data and isinstance(data, dict) and "requestId" in data:
                        self.socketio.emit(
                            "response",
                            {**response, "requestId": data["requestId"]},
                            to=request.sid,
                        )
                    return response

                logger.info(
                    f"Stream Deck: Template action requested - {template_name}.{action_name} (event: {event_name})"
                )

                # Emit the template action event to all connected clients
                event_data = {
                    "templateName": template_name,
                    "actionName": action_name,
                    "eventName": event_name,
                    "actionData": action_data,
                }

                self.socketio.emit("streamdeck_template_action", event_data)

                # Also emit specific template events for backward compatibility
                self.socketio.emit(f"{template_name}_{action_name}", action_data)

                # Emit event-specific events if event_name is different from action_name
                if event_name != action_name:
                    self.socketio.emit(f"{template_name}_{event_name}", action_data)

                logger.debug(
                    f"Stream Deck: Emitted template action events for {template_name}.{action_name}"
                )

                response = {
                    "success": True,
                    "templateName": template_name,
                    "actionName": action_name,
                    "eventName": event_name,
                    "message": f"Executed {template_name}.{action_name} (event: {event_name})",
                }

                # Handle SocketIO client response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error executing template action: {str(e)}",
                    exc_info=True,
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to execute template action",
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        @self.socketio.on("streamdeck_get_template_actions")
        def handle_streamdeck_get_template_actions(data=None):
            """Handle SocketIO event to get available template actions from template configurations"""
            try:
                logger.info("Stream Deck: SocketIO get_template_actions event received")
                actions_list = []
                template_configs = self.template_config_parser.get_config_files()
                logger.debug(
                    f"Stream Deck: Found {len(template_configs)} template configs"
                )

                for template_name in template_configs:
                    try:
                        # Load template configuration with Stream Deck options
                        config = self.template_config_parser.load_config(
                            template_name, include_streamdeck_options=True
                        )

                        if isinstance(config, dict) and "streamdeck_options" in config:
                            streamdeck_options = config["streamdeck_options"]

                            if "actions" in streamdeck_options:
                                template_actions = streamdeck_options["actions"]

                                for (
                                    action_key,
                                    action_config,
                                ) in template_actions.items():
                                    if isinstance(action_config, dict):
                                        action_info = {
                                            "template_name": template_name,
                                            "action_key": action_key,
                                            "action_name": action_config.get(
                                                "name", action_key
                                            ),
                                            "description": action_config.get(
                                                "description", ""
                                            ),
                                            "event": action_config.get(
                                                "event", f"{template_name}_{action_key}"
                                            ),
                                            "default_data": action_config.get(
                                                "default_data", {}
                                            ),
                                            "template_description": streamdeck_options.get(
                                                "description", ""
                                            ),
                                            "category": "streamdeck_action",
                                        }

                                        actions_list.append(action_info)

                        # Also check for dynamic controls that can be used as actions
                        if isinstance(config, dict) and "dynamic_controls" in config:
                            dynamic_controls = config["dynamic_controls"]

                            if "elements" in dynamic_controls:
                                for element in dynamic_controls["elements"]:
                                    if (
                                        isinstance(element, dict)
                                        and "action" in element
                                    ):
                                        element_type = element.get("type", "")
                                        action_name = element.get("action", "")

                                        # Create action info for dynamic controls
                                        action_info = {
                                            "template_name": template_name,
                                            "action_key": f"{template_name}_{action_name}",
                                            "action_name": element.get(
                                                "label", action_name
                                            ),
                                            "description": element.get(
                                                "description", ""
                                            ),
                                            "event": f"{template_name}_{action_name}",
                                            "default_data": {},
                                            "element_type": element_type,
                                            "category": "dynamic_control",
                                        }

                                        # Add type-specific data based on element configuration
                                        if element_type == "counter_control":
                                            if action_name in [
                                                "increment",
                                                "counter_increment",
                                            ] or action_name.endswith("_increment"):
                                                action_info["default_data"] = {
                                                    "action": "increment"
                                                }
                                            elif action_name in [
                                                "decrement",
                                                "counter_decrement",
                                            ] or action_name.endswith("_decrement"):
                                                action_info["default_data"] = {
                                                    "action": "decrement"
                                                }
                                            elif action_name in [
                                                "reset",
                                                "counter_reset",
                                            ] or action_name.endswith("_reset"):
                                                action_info["default_data"] = {
                                                    "action": "reset"
                                                }
                                        elif element_type in [
                                            "number_input",
                                            "text_input",
                                        ]:
                                            if "value" in element:
                                                if element_type == "number_input":
                                                    action_info["default_data"] = {
                                                        "value": element.get("value", 0)
                                                    }
                                                else:
                                                    action_info["default_data"] = {
                                                        "text": element.get("value", "")
                                                    }
                                        elif element_type == "button":
                                            action_info["default_data"] = {}

                                        actions_list.append(action_info)

                    except Exception as e:
                        logger.warning(
                            f"Error processing template {template_name}: {str(e)}"
                        )
                        continue

                logger.debug(
                    f"Stream Deck: Found {len(actions_list)} template actions across {len(template_configs)} templates"
                )

                response = {
                    "success": True,
                    "actions": actions_list,
                    "count": len(actions_list),
                    "templates_count": len(template_configs),
                    "message": f"Found {len(actions_list)} actions from {len(template_configs)} templates",
                }

                # Handle SocketIO client response format
                logger.debug(
                    f"Stream Deck: Sending template actions response to SID {request.sid}: {response}"
                )
                if data and isinstance(data, dict) and "requestId" in data:
                    # Emit specific response event for template actions list
                    self.socketio.emit(
                        "streamdeck_template_actions_response", response, to=request.sid
                    )
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error getting template actions: {str(e)}",
                    exc_info=True,
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "actions": [],
                    "count": 0,
                    "message": "Failed to load template actions",
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        @self.socketio.on("streamdeck_check_connection")
        def handle_streamdeck_check_connection(data=None):
            """Handle SocketIO event to check server connection and status"""
            try:
                global ALERTS_PAUSED
                global web_engine_running

                response = {
                    "success": True,
                    "connected": True,
                    "server_running": web_engine_running,
                    "alerts_paused": ALERTS_PAUSED,
                    "timestamp": datetime.now().isoformat(),
                    "message": "Mycelian server is running and responsive",
                }

                # Handle SocketIO client response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error in connection check: {str(e)}", exc_info=True
                )
                error_response = {
                    "success": False,
                    "connected": False,
                    "error": str(e),
                    "message": "Server error occurred",
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        @self.socketio.on("streamdeck_get_template_configs")
        def handle_streamdeck_get_template_configs(data=None):
            """Handle SocketIO event to get template configurations for Stream Deck integration"""
            try:
                # Get template configurations with Stream Deck options
                streamdeck_configs = (
                    self.template_config_parser.get_streamdeck_configs()
                )

                # Also include templates with dynamic controls as fallback
                dynamic_configs = {}
                all_configs = self.template_config_parser.get_config_files()

                for config_name in all_configs:
                    if config_name not in streamdeck_configs:
                        try:
                            config = self.template_config_parser.load_config(
                                config_name, include_dynamic_controls=True
                            )
                            if (
                                isinstance(config, dict)
                                and "dynamic_controls" in config
                            ):
                                dynamic_configs[config_name] = config
                                logger.debug(
                                    f"Included template with dynamic controls: {config_name}"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Error loading dynamic controls for {config_name}: {str(e)}"
                            )

                # Merge both types of configs
                all_template_configs = {**streamdeck_configs, **dynamic_configs}

                response = {
                    "success": True,
                    "data": all_template_configs,
                    "count": len(all_template_configs),
                }

                # Handle SocketIO client response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return response

            except Exception as e:
                logger.error(
                    f"Stream Deck: Error getting template configs: {str(e)}",
                    exc_info=True,
                )
                error_response = {
                    "success": False,
                    "error": str(e),
                    "data": {},
                    "count": 0,
                }

                # Handle SocketIO client error response format
                if data and isinstance(data, dict) and "requestId" in data:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=request.sid,
                    )
                else:
                    return error_response

        # Add response handler for SocketIO client responses
        @self.socketio.on("response")
        def handle_response(data):
            """Handle response events from SocketIO clients"""
            # This handler is mainly for logging - responses are handled by the specific handlers
            logger.debug(f"Received response event: {data}")

        logger.info("Stream Deck WebSocket handlers registered successfully")

    def run(self):
        """Run the Flask server"""
        global web_engine_running
        try:
            logger.debug(f"Starting WebEngine server on {self.host}:{self.port}")
            self.is_running = True
            web_engine_running = True
            # Enable debug mode for template reloading, but disable the reloader to avoid conflicts with our threading
            self.socketio.run(
                self.app,
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
            )
        except Exception as e:
            logger.error(f"Error running WebEngine server: {str(e)}", exc_info=True)
            self.is_running = False
            web_engine_running = False
        finally:
            self.is_running = False
            web_engine_running = False
            logger.debug("WebEngine server stopped")

    def start(self):
        """Start the WebEngine server in a separate thread"""
        global web_engine_running, web_engine_instance
        if self.server_thread is None or not self.server_thread.is_alive():
            self.server_thread = threading.Thread(target=self.run)
            self.server_thread.daemon = True
            self.server_thread.start()
            logger.debug("WebEngine server thread started")
            # Wait a moment for the server to start
            time.sleep(1)
            web_engine_running = self.is_running
            web_engine_instance = self  # Set global instance
        else:
            logger.warning("WebEngine server thread already running")

    def stop(self):
        """Stop the WebEngine server"""
        global web_engine_running
        if self.is_running:
            logger.debug("Stopping WebEngine server")
            try:
                # Use a separate thread to stop the server to avoid blocking
                def stop_server():
                    try:
                        # Try to check if we're in a request context before stopping
                        # Since we're in a separate thread, this should generally be False
                        in_request_context = False
                        try:
                            from flask import has_request_context

                            in_request_context = has_request_context()
                        except Exception:
                            # If we can't check the context (e.g., working outside Flask context),
                            # assume we're not in a request context and proceed with stopping
                            in_request_context = False

                        if in_request_context:
                            logger.debug("In request context, skipping socketio.stop()")
                        else:
                            self.socketio.stop()
                    except Exception as e:
                        logger.error(f"Error stopping socketio: {str(e)}")

                stop_thread = threading.Thread(target=stop_server)
                stop_thread.daemon = True
                stop_thread.start()

                # Wait for the stop thread to finish
                stop_thread.join(timeout=5)

                self.is_running = False
                web_engine_running = False
                logger.debug("WebEngine server stopped")
            except Exception as e:
                logger.error(
                    f"Error stopping WebEngine server: {str(e)}", exc_info=True
                )
        else:
            logger.warning("WebEngine server not running")

    def is_alive(self):
        """Check if the web server is running"""
        return (
            self.is_running
            and self.server_thread is not None
            and self.server_thread.is_alive()
        )

    def get_available_source_urls(self):
        """Get a list of all available source URLs (excluding hidden templates)"""
        urls = []

        try:
            # Get base URL
            base_url = f"http://{self.host}:{self.port}"

            # Use our tracked template routes instead of iterating through Flask rules
            for template_name in sorted(self._registered_template_routes):
                # Check if the template is hidden
                if (
                    hasattr(self, "template_config_parser")
                    and self.template_config_parser
                ):
                    if self.template_config_parser.is_config_hidden(template_name):
                        logger.debug(f"Skipping hidden template: {template_name}")
                        continue

                url = f"{base_url}/{template_name}"
                urls.append(
                    {
                        "name": template_name,
                        "url": url,
                        "type": "template",
                        "description": f"HTML template: {template_name}.html",
                    }
                )

            # Get templates with dynamic control handlers
            if hasattr(self, "_registered_dynamic_handlers"):
                dynamic_templates = set()
                for handler in self._registered_dynamic_handlers:
                    if "_" in handler:
                        template_name = handler.split("_")[0]
                        dynamic_templates.add(template_name)

                # Add dynamic indicator to existing templates or create new entries
                for template_name in dynamic_templates:
                    # Check if the template is hidden
                    if (
                        hasattr(self, "template_config_parser")
                        and self.template_config_parser
                    ):
                        if self.template_config_parser.is_config_hidden(template_name):
                            logger.debug(
                                f"Skipping dynamic handler for hidden template: {template_name}"
                            )
                            continue

                    # Find existing template entry and update it
                    existing_template = next(
                        (url for url in urls if url["name"] == template_name), None
                    )
                    if existing_template:
                        existing_template["type"] = "template_with_controls"
                        existing_template["description"] = (
                            f"HTML template with controls: {template_name}.html"
                        )
                        existing_template["has_dynamic_controls"] = True
                    else:
                        # Template not in registered routes but has dynamic controls
                        # This might happen if the template file was deleted but handlers remain
                        logger.debug(
                            f"Found dynamic handlers for template not in routes: {template_name}"
                        )

            # Add template auto-reload status to the response
            reload_status = {
                "name": "_template_auto_reload_status",
                "url": None,
                "type": "system_info",
                "description": f'Template auto-reload: {"Enabled" if self.app.config.get("TEMPLATES_AUTO_RELOAD", False) else "Disabled"}',
                "auto_reload_enabled": self.app.config.get(
                    "TEMPLATES_AUTO_RELOAD", False
                ),
                "template_dir": str(self.template_dir),
            }
            urls.append(reload_status)

            logger.debug(
                f"Found {len(urls)-1} available source URLs (hidden templates excluded)"
            )
            return urls

        except Exception as e:
            logger.error(
                f"Error getting available source URLs: {str(e)}", exc_info=True
            )
            return []

    def broadcast_theme_update(self, theme_css: str, theme_name: str, theme_type: str) -> bool:
        """Broadcast a theme update to all connected WebSocket clients.

        Args:
            theme_css: CSS variable string generated by generate_css_variables()
            theme_name: Name of the theme being applied
            theme_type: Theme type ('dark' or 'light')

        Returns:
            True if broadcast was successful, False otherwise
        """
        try:
            self.socketio.emit(
                "theme_update",
                {
                    "css": theme_css,
                    "theme_name": theme_name,
                    "theme_type": theme_type,
                },
            )
            logger.info(f"Broadcast theme update: {theme_name} ({theme_type})")
            return True
        except Exception as e:
            logger.error(f"Error broadcasting theme update: {str(e)}", exc_info=True)
            return False

    def clear_chat(self):
        """
        Trigger a 'clear-chat' websocket event to clear all chat messages
        """
        try:
            self.socketio.emit("clear-chat")
            logger.debug("Sent clear-chat event")
            return True
        except Exception as e:
            logger.error(f"Error sending clear-chat: {str(e)}", exc_info=True)
            return False

    def remove_messages(self, message_ids):
        """
        Trigger a 'remove-messages' websocket event to remove specific messages

        Args:
            message_ids (list): List of message IDs to remove
        """
        try:
            self.socketio.emit("remove-messages", message_ids)
            logger.debug(f"Sent remove-messages: {message_ids}")
            return True
        except Exception as e:
            logger.error(f"Error sending remove-messages: {str(e)}", exc_info=True)
            return False

            logger.debug(f"Sent remove-messages: {message_ids}")
            return True
        except Exception as e:
            logger.error(f"Error sending remove-messages: {str(e)}", exc_info=True)
            return False

            return True
        except Exception as e:
            logger.error(f"Error sending remove-messages: {str(e)}", exc_info=True)
            return False

            return True
        except Exception as e:
            logger.error(f"Error sending remove-messages: {str(e)}", exc_info=True)
            return False

            return True
        except Exception as e:
            logger.error(f"Error sending remove-messages: {str(e)}", exc_info=True)
            return False
