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
import queue
import random
import re
import sys
import faulthandler
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Eventlet emits a deprecation warning on import. Flask-SocketIO still relies on
# it here, so silence the noise (a migration off eventlet is tracked separately).
warnings.filterwarnings("ignore", message=r"\s*Eventlet is deprecated")

import eventlet
import eventlet.green.select
import eventlet.green.socket
import eventlet.green.subprocess
import eventlet.green.threading
import eventlet.green.time
import eventlet.wsgi
from engineio.async_drivers import gevent
from engineio.payload import Payload

# Default is 16; OBS browser-source reconnect bursts can exceed that in one polling POST.
Payload.max_decode_packets = 128

from flask import (
    Flask,
    make_response,
    render_template,
    render_template_string,
    request,
    send_from_directory,
)
from flask_socketio import SocketIO, emit

from . import alertutils, database_manager, statistics_manager, twitch
from .dataobjects import state_manager  # To access live PSN data
from .path_utils import (
    get_assets_path,
    get_data_path,
    get_static_path,
    get_template_path,
)
from .psnapi import PSNData  # For type hinting if needed, and default object
from .streamdeck_plugin_utils import enqueue_streamdeck_connector_event
from .streamdeck_template_dispatch import (
    coerce_streamdeck_action_data as _coerce_streamdeck_action_data,
)
from .streamdeck_template_dispatch import (
    merged_streamdeck_options_payload as _merged_streamdeck_options_payload,
)
from .streamdeck_template_dispatch import (
    plan_streamdeck_template_action_emit,
)
from .streamdeck_template_dispatch import (
    resolve_streamdeck_options_action as _resolve_streamdeck_options_action_fn,
)
from .template_config_parser import (
    TemplateConfigParser,
    resolve_dynamic_control_values_from_elements,
)
from .theme_manager import generate_css_variables, get_theme_manager

logger = logging.getLogger(__name__)


def _dynamic_counter_control_default_data(element: Dict[str, Any]) -> Dict[str, Any]:
    """Default socket payload for counter_control dynamic controls (connectors / Stream Deck)."""
    target_id = element.get("target_counter_id")
    if target_id:
        try:
            step = max(1, int(element.get("step", 1)))
        except (TypeError, ValueError):
            step = 1
        return {
            "target_counter_id": target_id,
            "operation": "increment",
            "delta": {"kind": "fixed", "value": step},
        }
    action_name = str(element.get("action") or "")
    if action_name in ("increment", "counter_increment") or action_name.endswith(
        "_increment"
    ):
        return {"action": "increment"}
    if action_name in ("decrement", "counter_decrement") or action_name.endswith(
        "_decrement"
    ):
        return {"action": "decrement"}
    if action_name in ("reset", "counter_reset") or action_name.endswith("_reset"):
        return {"action": "reset"}
    return {}


# Explicit MIME types for media under /assets — OS mimetypes may miss extensions
# (e.g. .wav on Windows), yielding application/octet-stream and breaking <audio>
# in strict embedded browsers (OBS Browser Source / CEF).
_ASSET_MEDIA_MIMETYPES = {
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def _mimetype_for_asset_filename(filename: str) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    return _ASSET_MEDIA_MIMETYPES.get(ext)


# Mock data pools shared with :mod:`spore_studio.preview_mocks` for manual
# preview toolbar emits (Custom Sources + Spore Studio).
_DEMO_USERNAMES = (
    "PixelPanda", "NeonNova", "TacoTuesday", "ShinyHaxor", "QwertyKnight",
    "MidnightMango", "EmberFox", "GlitchWizard", "VelvetVortex", "OptimusByte",
    "RetroRogue", "CelestialCat", "BlueberryBoss", "QuantumQuokka", "ZenithZen",
    "FrostyFlame", "LunarLynx", "RubyRanger", "SableSpark", "TwilightTitan",
)
_DEMO_CHAT_MESSAGES = (
    "GG!",
    "Let's gooo!",
    "That was insane!",
    "Pog moment!",
    "How did you do that?!",
    "First time catching the stream, loving the vibes",
    "Hello chat o/",
    "Are we doing the next quest?",
    "I just subscribed!",
    "Been here since the start, keep it up :)",
    "What's the build?",
    "MVP play right there",
    "Sub goal when?",
    "BibleThump that was close",
    "Big W energy",
    "Take a snack break <3",
    "Lurking but watching",
    "rofl",
    "monkaW",
    "PogChamp content",
)
_DEMO_GAME_TITLES = (
    "Final Fantasy VII Rebirth",
    "Elden Ring",
    "Hades II",
    "Stardew Valley",
    "Minecraft",
    "Just Chatting",
    "Sekiro: Shadows Die Twice",
    "Baldur's Gate 3",
    "Counter-Strike 2",
    "Apex Legends",
)
_DEMO_CHAT_COLORS = (
    "#FF4500", "#1E90FF", "#00CED1", "#FF69B4", "#9ACD32",
    "#FFA500", "#9370DB", "#FFD700", "#20C997", "#FF6B6B",
)
# Alert presets used by manual preview mock emits. Each entry is the body
# of an ``alerts_play_alert`` / ``next_alert``-style payload.
# ``username``, ``timestamp`` and a default ``duration`` are filled in per-cycle.
_DEMO_ALERT_PRESETS = (
    {"alert_type": "sub", "tier": "1000"},
    {"alert_type": "bit", "amt_cheered": 200, "message": "Awesome stream!"},
    {"alert_type": "raid", "raider_count": 25},
    {"alert_type": "donation", "amount": 5.0, "currency": "USD",
     "message": "Keep it up <3"},
    {"alert_type": "follow"},
    {"alert_type": "giftsub", "gift_qty": 3, "tier": "1000"},
    {"alert_type": "resub", "tier": "1000", "cumulative_months": 7,
     "message": "7 months strong!"},
    {"alert_type": "point", "alert_name": "Hydrate Reminder",
     "message": "Time to drink water!"},
)

# Snippet appended to a template's HTML response when served in preview mode
# (i.e. with ``__preview_token``). Forces elements opted-in via the
# ``mycelian-preview-show`` CSS class to remain visible in the previewer
# even when the template's normal data-driven logic would hide them
# (e.g. FF7 enemy panel when no game is attached, Spotify panel when
# nothing is playing). The override is "display only" — opacity / visibility
# / animation states are intentionally untouched so entrance fades and
# legitimate transient UI keep working.
#
# Real OBS overlays never receive this snippet (no preview token, no
# cookie, no injection).
MYCELIAN_PREVIEW_HELPER_HTML = """
<style id="__mycelian_preview_helper_css">
/* reserved for future preview-only CSS overrides */
</style>
<script id="__mycelian_preview_helper_js">
(function () {
  if (window.__mycelianPreviewHelperReady) { return; }
  window.__mycelianPreviewHelperReady = true;
  window.__mycelianPreviewSettings = window.__mycelianPreviewSettings || {
    enable_preview_sounds: true,
    show_mock_toolbar: true
  };
  (function patchPreviewAudio() {
    if (window.__mycelianPreviewAudioPatched) { return; }
    window.__mycelianPreviewAudioPatched = true;
    var origPlay = HTMLAudioElement.prototype.play;
    HTMLAudioElement.prototype.play = function () {
      var s = window.__mycelianPreviewSettings;
      if (s && s.enable_preview_sounds === false) {
        return Promise.resolve(undefined);
      }
      return origPlay.apply(this, arguments);
    };
  })();
  var SEL = ".mycelian-preview-show";
  function unhide(el) {
    if (!el || !el.classList) { return; }
    if (el.classList.contains("hidden")) { el.classList.remove("hidden"); }
    if (el.style && el.style.display === "none") {
      el.style.removeProperty("display");
    }
  }
  function bindAttrObserver(el, attrObs) {
    if (!el || el.__mycelianPreviewBound) { return; }
    el.__mycelianPreviewBound = true;
    attrObs.observe(el, { attributes: true, attributeFilter: ["class", "style"] });
  }
  function init() {
    var attrObs = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var t = muts[i].target;
        if (t && t.matches && t.matches(SEL)) { unhide(t); }
      }
    });
    document.querySelectorAll(SEL).forEach(function (el) {
      unhide(el);
      bindAttrObserver(el, attrObs);
    });
    var treeObs = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (!n || n.nodeType !== 1) { continue; }
          if (n.matches && n.matches(SEL)) {
            unhide(n);
            bindAttrObserver(n, attrObs);
          }
          if (n.querySelectorAll) {
            n.querySelectorAll(SEL).forEach(function (c) {
              unhide(c);
              bindAttrObserver(c, attrObs);
            });
          }
        }
      }
    });
    treeObs.observe(document.documentElement, { childList: true, subtree: true });
  }
  var __mycelianTemplateLoadTemplateConfig =
    typeof loadTemplateConfig === "function" ? loadTemplateConfig : null;
  function mycelianSyncStylesFromPreviewHtml(html) {
    var parsed = new DOMParser().parseFromString(html, "text/html");
    var head = document.head;
    if (!head) { return; }
    var keep = { __mycelian_preview_helper_css: true };
    head.querySelectorAll("style").forEach(function (node) {
      var id = node.id || "";
      if (keep[id]) { return; }
      if (id.indexOf("mycelian-") === 0 && id.indexOf("-preview-dynamic") !== -1) { return; }
      node.parentNode.removeChild(node);
    });
    parsed.head.querySelectorAll("style").forEach(function (node) {
      var id = node.id || "";
      if (id === "__mycelian_preview_helper_css") { return; }
      head.appendChild(document.importNode(node, true));
    });
  }
  async function mycelianGenericLoadTemplateConfig() {
    var qs = window.location.search || "";
    if (qs.indexOf("__preview_token=") === -1) { return; }
    var cbIdx = qs.indexOf("&_cb=");
    var baseQs = cbIdx >= 0 ? qs.substring(0, cbIdx) : qs;
    var url = window.location.pathname + baseQs + "&_cb=" + Date.now();
    try {
      var resp = await fetch(url, { credentials: "same-origin" });
      if (!resp.ok) {
        console.warn("Generic preview config refresh failed:", resp.status);
        return;
      }
      mycelianSyncStylesFromPreviewHtml(await resp.text());
    } catch (err) {
      console.warn("Generic preview config refresh failed:", err);
    }
  }
  window.loadTemplateConfig = async function () {
    if (__mycelianTemplateLoadTemplateConfig) {
      return __mycelianTemplateLoadTemplateConfig.apply(this, arguments);
    }
    return mycelianGenericLoadTemplateConfig();
  };
  function attachPreviewSocketHooks() {
    var n = 0;
    var id = setInterval(function () {
      n++;
      var sock = typeof socket !== "undefined" ? socket : null;
      if (!sock || typeof sock.on !== "function") {
        if (n > 120) { clearInterval(id); }
        return;
      }
      clearInterval(id);
      if (sock.__mycelianPreviewHooks) { return; }
      sock.__mycelianPreviewHooks = true;
      sock.on("mycelian_preview_config_refresh", function () {
        if (typeof window.loadTemplateConfig === "function") {
          window.loadTemplateConfig();
        }
      });
      sock.on("mycelian_preview_settings", function (data) {
        window.__mycelianPreviewSettings = data || {};
      });
      try {
        var qs = window.location.search || "";
        var tokMatch = /(?:\\?|&)__preview_token=([^&]+)/.exec(qs);
        if (tokMatch) {
          sock.emit("mycelian_preview_register", {
            token: decodeURIComponent(tokMatch[1]),
          });
        }
      } catch (regErr) {}
      try {
        sock.emit("mycelian_preview_client_ready");
      } catch (err) {}
    }, 50);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  attachPreviewSocketHooks();
})();
</script>
"""


ALERT_PLAYING = False
ALERTS_PAUSED = False
ALERTS_MUTED = False


def _sync_mute_button_state() -> None:
    """Sync NiceGUI activity feed mute button with ALERTS_MUTED."""
    try:
        from modules.uiwindows.activity_feed import sync_mute_button_state

        sync_mute_button_state()
    except Exception as e:
        logger.debug(f"Could not sync mute button state: {e}")


# Monotonic id for the current queued alert; only matching client alert_complete advances the queue.
_alert_queue_seq_lock = threading.Lock()
_alert_queue_seq = 0
EXPECTED_ALERT_COMPLETE_SEQ: int | None = None


def assign_next_alert_queue_seq() -> int:
    """Reserve the next queue_seq for process_alert; clients must echo this in alert_complete."""
    global _alert_queue_seq, EXPECTED_ALERT_COMPLETE_SEQ
    with _alert_queue_seq_lock:
        _alert_queue_seq += 1
        EXPECTED_ALERT_COMPLETE_SEQ = _alert_queue_seq
        return _alert_queue_seq

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
        # Custom Sources preview demo loops: sid -> stop flag (legacy —
        # the auto-demo loop has been removed in favour of manual mock
        # buttons in the Spore Studio preview dialog. Kept around so
        # disconnect handlers don't crash on a missing attribute.)
        self._preview_demo_stop: Dict[str, bool] = {}
        # Spore Studio preview iframe: token -> sid (lets the manual
        # mock-event endpoint resolve which client to emit to). Inverse
        # mapping (sid -> token) is maintained so disconnect cleanup is
        # O(1) without scanning the dict.
        self._preview_iframe_sids: Dict[str, str] = {}
        self._preview_iframe_tokens: Dict[str, str] = {}
        # template_name -> {mtime, cfg} for Stream Deck hot-path config resolution
        self._streamdeck_config_cache: Dict[str, Dict[str, Any]] = {}

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

        # Template auto-reload: dev / unfrozen builds only (OBS sources stat every HTML file otherwise)
        _jinja_auto_reload = bool(
            os.environ.get("MYCELIAN_DEV")
            or not getattr(sys, "frozen", False)
        )
        self.app.config["TEMPLATES_AUTO_RELOAD"] = _jinja_auto_reload
        self.app.config["SEND_FILE_MAX_AGE_DEFAULT"] = (
            0  # Disable caching for development
        )
        self.app.jinja_env.auto_reload = _jinja_auto_reload

        # Configure SocketIO with increased limits to prevent "Too many packets in payload" errors
        # This can happen during startup when there are rapid bursts of Socket.IO events
        # Cross-thread template control emits (NiceGUI -> Web Engine gevent loop)
        self._template_control_queue: queue.Queue = queue.Queue()
        self._template_control_emit_worker_started = False
        self._template_control_emit_worker_lock = threading.Lock()

        # Socket.IO client tracking (observability / heartbeat)
        self._socket_connected_count = 0
        self._socket_connected_lock = threading.Lock()
        self._heartbeat_task_started = False
        self._heartbeat_lock = threading.Lock()

        # Watchdog: the gevent heartbeat updates this timestamp; a native thread
        # watches it and dumps all thread stacks if the gevent hub stops ticking
        # (the signature of a freeze where OBS sources / Stream Deck stop).
        self._last_gevent_heartbeat: Optional[float] = None
        self._watchdog_thread: Optional[threading.Thread] = None

        # Short-TTL cache for GET /api/all-template-configs (OBS refresh storms)
        self._all_template_configs_cache: Optional[
            Tuple[str, float, bytes]
        ] = None
        self._all_template_configs_cache_lock = threading.Lock()
        self._all_template_configs_slow_log_at = 0.0

        # Bounded Twitch API worker (avoids per-request thread + event loop)
        self._twitch_api_queue: queue.Queue = queue.Queue()
        self._twitch_api_worker_started = False
        self._twitch_api_worker_lock = threading.Lock()

        # Spore Studio assets watcher: poll only these template asset folders
        self._assets_watch_templates_lock = threading.Lock()
        self._assets_watch_templates: set = set()

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
            if logger.isEnabledFor(logging.DEBUG):
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
            _static_debug = logger.isEnabledFor(logging.DEBUG)
            if _static_debug:
                logger.debug(
                    "Custom route: Attempting to serve static file: %s", filename
                )
            assets_path = get_assets_path()
            full_path = os.path.join(assets_path, filename)

            try:
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    directory_path = os.path.dirname(full_path)
                    filename_only = os.path.basename(full_path)
                    mimetype = _mimetype_for_asset_filename(filename_only)
                    if mimetype:
                        return send_from_directory(
                            directory_path, filename_only, mimetype=mimetype
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
            assets_path = get_assets_path()
            full_path = os.path.join(assets_path, filename)

            try:
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    directory_path = os.path.dirname(full_path)
                    filename_only = os.path.basename(full_path)
                    mimetype = _mimetype_for_asset_filename(filename_only)
                    if mimetype:
                        return send_from_directory(
                            directory_path, filename_only, mimetype=mimetype
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
                preview_tok = request.cookies.get("mycelian_preview_token")

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
                            dynamic_controls = (
                                resolve_dynamic_control_values_from_elements(config)
                            )
                            if (
                                isinstance(dynamic_controls, dict)
                                and "elements" in dynamic_controls
                            ):
                                if dynamic_controls[
                                    "elements"
                                ]:  # Only include if there are elements
                                    self._merge_preview_session_into_config(
                                        preview_tok, config_name, dynamic_controls
                                    )
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
            t0 = time.time()
            try:
                preview_tok = request.cookies.get("mycelian_preview_token") or ""
                payload_bytes, cache_hit = self._get_all_template_configs_cached(
                    preview_tok
                )
                elapsed = time.time() - t0
                if elapsed > 0.5 and time.time() >= self._all_template_configs_slow_log_at:
                    self._all_template_configs_slow_log_at = time.time() + 30.0
                    try:
                        template_count = len(
                            json.loads(payload_bytes.decode("utf-8"))
                        )
                    except Exception:
                        template_count = -1
                    logger.warning(
                        "serve_all_template_configs slow: %.3fs (%s templates, "
                        "cache_hit=%s cache_key=%r)",
                        elapsed,
                        template_count,
                        cache_hit,
                        preview_tok[:8] if preview_tok else "",
                    )
                return (
                    payload_bytes,
                    200,
                    {"Content-Type": "application/json"},
                )

            except Exception as e:
                logger.error(
                    f"Error serving all template configs: {str(e)}", exc_info=True
                )
                return (
                    {"error": "Failed to load template configurations"},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/template-config/<template_name>")
        def serve_single_template_config(template_name: str):
            """Serve one template's raw JSON (preview hot-reload without loading all configs)."""
            try:
                name = (template_name or "").strip()
                if not name or "/" in name or "\\" in name:
                    return {"error": "Invalid template name"}, 400
                preview_tok = request.cookies.get("mycelian_preview_token")
                config = self.template_config_parser.load_config(
                    name, include_dynamic_controls=False
                )
                self._merge_preview_session_into_config(preview_tok, name, config)
                return config, 200, {"Content-Type": "application/json"}
            except Exception as e:
                logger.error(
                    "Error serving template config for %s: %s",
                    template_name,
                    e,
                    exc_info=True,
                )
                return (
                    {"error": "Failed to load template configuration"},
                    500,
                    {"Content-Type": "application/json"},
                )

        # ------------------------------------------------------------------
        # Spore Studio — visual template editor endpoints.
        # The editor itself is a static HTML/JS bundle under
        # assets/default_assets/spore_studio/ that runs inside an iframe in
        # the NiceGUI tab. These endpoints are the bridge between that
        # bundle and the Mycelian filesystem.
        # ------------------------------------------------------------------
        @self.app.route("/_spore_studio_editor")
        def serve_spore_studio_editor():
            """Serve the Spore Studio editor shell HTML."""
            editor_path = get_assets_path(
                os.path.join("default_assets", "spore_studio", "editor.html")
            )
            if not os.path.isfile(editor_path):
                return ("Spore Studio editor bundle missing.", 500)
            try:
                directory = os.path.dirname(editor_path)
                return send_from_directory(directory, "editor.html")
            except Exception as e:
                logger.error("Error serving Spore Studio editor: %s", e)
                return (f"Error serving editor: {e}", 500)

        @self.app.route("/api/spore-studio/events")
        def serve_spore_studio_events():
            """Return the curated event + action registry for the binding picker."""
            try:
                from .spore_studio import event_registry as _ev

                return _ev.get_event_registry(), 200, {
                    "Content-Type": "application/json"
                }
            except Exception as e:
                logger.error("Spore Studio events endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/data-sources")
        def serve_spore_studio_data_sources():
            """Return curated data sources for counters and data displays."""
            try:
                from .spore_studio import data_source_registry as _ds

                return _ds.get_data_source_registry(), 200, {
                    "Content-Type": "application/json"
                }
            except Exception as e:
                logger.error("Spore Studio data-sources endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/control-actions")
        def serve_spore_studio_control_actions():
            """Return curated dynamic control actions for Source Controls."""
            try:
                from .spore_studio import control_action_registry as _ca

                return _ca.get_control_action_registry(), 200, {
                    "Content-Type": "application/json"
                }
            except Exception as e:
                logger.error("Spore Studio control-actions endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/templates")
        def serve_spore_studio_templates():
            """List all templates with a Spore Studio sidecar plus legacy ones."""
            try:
                from .spore_studio import save_pipeline as _sp

                spore, legacy = _sp.list_spore_templates()
                return (
                    {"spore_templates": spore, "legacy_templates": legacy},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error("Spore Studio templates endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/model/<template_name>")
        def serve_spore_studio_model(template_name):
            """Return the editor model (sidecar or legacy fallback) for a template."""
            try:
                from .spore_studio import template_parser_back as _tpb

                model = _tpb.parse_existing(template_name)
                if isinstance(model, dict) and not model.get("legacy"):
                    from .spore_studio import preview_mocks as _pm

                    model = dict(model)
                    model["preview_mocks"] = _pm.derive_preview_mocks(model)
                return model, 200, {"Content-Type": "application/json"}
            except Exception as e:
                logger.error("Spore Studio model endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/fonts")
        def serve_spore_studio_fonts():
            """Return fonts from assets/default_assets/fonts for the editor picker."""
            try:
                from .spore_studio import fonts_registry as _fr

                return _fr.get_font_registry(), 200, {
                    "Content-Type": "application/json"
                }
            except Exception as e:
                logger.error("Spore Studio fonts endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/assets/<template_name>")
        def serve_spore_studio_assets(template_name):
            """Return the asset tree for a single template (drives the asset browser)."""
            try:
                from .spore_studio import assets_watcher as _aw

                snapshot = _aw.request_snapshot(template_name)
                return snapshot, 200, {"Content-Type": "application/json"}
            except Exception as e:
                logger.error("Spore Studio assets endpoint error: %s", e)
                return ({"error": str(e)}, 500, {"Content-Type": "application/json"})

        @self.app.route("/api/spore-studio/save", methods=["POST"])
        def save_spore_studio_template():
            """Persist an editor model and regenerate HTML + JSON config."""
            try:
                from .spore_studio import save_pipeline as _sp

                payload = request.get_json(silent=True) or {}
                model = payload.get("model")
                if not isinstance(model, dict):
                    return (
                        {"error": "Request body must include a 'model' object."},
                        400,
                        {"Content-Type": "application/json"},
                    )
                try:
                    saved = _sp.save_template(model)
                except _sp.SporeStudioError as ex:
                    return (
                        {"error": str(ex)},
                        400,
                        {"Content-Type": "application/json"},
                    )
                return (
                    {"ok": True, "model": saved},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error("Spore Studio save error: %s", e, exc_info=True)
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route(
            "/api/spore-studio/preview/register", methods=["POST"]
        )
        def register_spore_studio_preview():
            """
            Register a preview session for the Spore Studio iframe.

            Ensures ``_preview_sessions[token]`` exists so the template GET handler
            sets ``mycelian_preview_token`` and links the websocket sid for mock
            emits. Preserves unsaved draft HTML from
            ``/api/spore-studio/preview/draft`` when the template name matches
            (:meth:`_register_spore_preview_session_keep_draft`).
            """
            try:
                payload = request.get_json(silent=True) or {}
                token = payload.get("token")
                template = payload.get("template")
                if not token or not template:
                    return (
                        {"error": "token and template are required"},
                        400,
                        {"Content-Type": "application/json"},
                    )
                self._register_spore_preview_session_keep_draft(
                    str(token), str(template)
                )
                self.register_assets_watch_template(str(template))
                return (
                    {"ok": True},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    "Spore Studio preview register error: %s",
                    e, exc_info=True,
                )
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route(
            "/api/spore-studio/preview/draft", methods=["POST"]
        )
        def spore_studio_preview_draft():
            """Compile unsaved editor model into the preview session (HTML + JSON)."""
            try:
                from .spore_studio import save_pipeline as _sp

                payload = request.get_json(silent=True) or {}
                token = payload.get("token")
                model = payload.get("model")
                if not token or not isinstance(model, dict):
                    return (
                        {"error": "token and model are required"},
                        400,
                        {"Content-Type": "application/json"},
                    )
                try:
                    draft_html, draft_config = _sp.compile_preview_draft(model)
                except _sp.SporeStudioError as ex:
                    return (
                        {"error": str(ex)},
                        400,
                        {"Content-Type": "application/json"},
                    )
                template_name = str(model.get("template_name") or "").strip()
                with self._preview_sessions_lock:
                    sess = self._preview_sessions.get(str(token))
                    if not isinstance(sess, dict) or sess.get("template") != template_name:
                        return (
                            {
                                "error": (
                                    "Preview session not registered for this "
                                    "template; open preview first."
                                )
                            },
                            400,
                            {"Content-Type": "application/json"},
                        )
                    sess["draft_html"] = draft_html
                    sess["draft_config"] = copy.deepcopy(draft_config)
                    sess["ts"] = time.time()
                return (
                    {"ok": True},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error("Spore Studio preview draft error: %s", e, exc_info=True)
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route(
            "/api/spore-studio/preview/release", methods=["POST"]
        )
        def release_spore_studio_preview():
            """Drop the preview session created by ``/preview/register``."""
            try:
                payload = request.get_json(silent=True) or {}
                token = payload.get("token")
                if not token:
                    return (
                        {"ok": True},
                        200,
                        {"Content-Type": "application/json"},
                    )
                with self._preview_sessions_lock:
                    self._preview_sessions.pop(str(token), None)
                self._preview_iframe_sids.pop(str(token), None)
                return (
                    {"ok": True},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    "Spore Studio preview release error: %s",
                    e, exc_info=True,
                )
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/spore-studio/notify", methods=["POST"])
        def proxy_spore_studio_notify():
            """
            Route Spore Studio iframe notifications through
            ``notification_engine.notify`` so they appear in the parent
            NiceGUI window's notification engine instead of being shown
            as bottom-center DOM toasts inside the iframe.

            The editor still keeps its own toast as a fallback for the
            case where this endpoint is unreachable (network blip during
            save, etc.).
            """
            try:
                from . import notification_engine as _ne

                payload = request.get_json(silent=True) or {}
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip():
                    return (
                        {"error": "message is required"},
                        400,
                        {"Content-Type": "application/json"},
                    )
                kind = payload.get("type") or "info"
                _ne.notify(str(message), type=str(kind))
                return (
                    {"ok": True},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    "Spore Studio notify proxy error: %s",
                    e, exc_info=True,
                )
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/spore-studio/preview/emit", methods=["POST"])
        def emit_spore_studio_preview_mock():
            """
            Emit a mock socket event into the Spore Studio preview iframe
            (``token``). Registry events use curated mock payloads; when the
            JSON body includes ``data`` or ``payload``, that object is emitted
            as-is (for Stream Deck actions and custom events).
            """
            try:
                payload = request.get_json(silent=True) or {}
                token = payload.get("token")
                event_name = payload.get("event")
                if not token or not event_name:
                    return (
                        {"error": "token and event are required"},
                        400,
                        {"Content-Type": "application/json"},
                    )
                custom = payload.get("data")
                if custom is None:
                    custom = payload.get("payload")
                alert_type = payload.get("alert_type")
                ok, err, emitted = self.emit_preview_mock(
                    str(token),
                    str(event_name),
                    custom,
                    alert_type=alert_type,
                )
                if not ok:
                    code = 404 if err and "No preview iframe" in err else 400
                    return (
                        {"error": err or "emit failed"},
                        code,
                        {"Content-Type": "application/json"},
                    )
                return (
                    {"ok": True, "event": emitted or str(event_name)},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    "Spore Studio preview emit error: %s", e, exc_info=True,
                )
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/spore-studio/preview/mocks", methods=["POST"])
        def spore_studio_preview_mocks():
            """Derive preview toolbar actions from an in-memory editor model."""
            try:
                from .spore_studio import preview_mocks as _pm

                payload = request.get_json(silent=True) or {}
                model = payload.get("model")
                if not isinstance(model, dict):
                    return (
                        {"error": "model object is required"},
                        400,
                        {"Content-Type": "application/json"},
                    )
                mocks = _pm.derive_preview_mocks(model)
                return (
                    {"preview_mocks": mocks},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    "Spore Studio preview mocks error: %s", e, exc_info=True,
                )
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/spore-studio/create", methods=["POST"])
        def create_spore_studio_template():
            """Create a fresh template (boilerplate or copy-from)."""
            try:
                from .spore_studio import save_pipeline as _sp

                payload = request.get_json(silent=True) or {}
                try:
                    model = _sp.create_template(
                        name=str(payload.get("name") or ""),
                        alert_system=str(payload.get("alert_system") or "queue"),
                        copy_from=payload.get("copy_from") or None,
                        design_width=int(payload.get("design_width") or 800),
                        design_height=int(payload.get("design_height") or 200),
                    )
                except _sp.SporeStudioError as ex:
                    return (
                        {"error": str(ex)},
                        400,
                        {"Content-Type": "application/json"},
                    )
                return (
                    {"ok": True, "model": model},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error("Spore Studio create error: %s", e, exc_info=True)
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/spore-studio/delete", methods=["POST"])
        def delete_spore_studio_template():
            """Delete a Spore Studio template (HTML, config, sidecar, assets)."""
            try:
                from .spore_studio import save_pipeline as _sp

                payload = request.get_json(silent=True) or {}
                try:
                    _sp.delete_template(str(payload.get("name") or ""))
                except _sp.SporeStudioError as ex:
                    return (
                        {"error": str(ex)},
                        400,
                        {"Content-Type": "application/json"},
                    )
                return (
                    {"ok": True},
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error("Spore Studio delete error: %s", e, exc_info=True)
                return (
                    {"error": str(e)},
                    500,
                    {"Content-Type": "application/json"},
                )

        # Add Stream Deck API endpoints
        @self.app.route("/api/streamdeck/toggle_alerts", methods=["POST"])
        def streamdeck_toggle_alerts():
            """Stream Deck endpoint to toggle alert pause/resume status"""
            try:
                logger.debug("Stream Deck: Toggle alerts requested")

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
                        raw_event_name = data.get("eventName")
                        use_client_event = (
                            raw_event_name is not None
                            and str(raw_event_name).strip() != ""
                        )
                        event_name = (
                            str(raw_event_name).strip()
                            if use_client_event
                            else action_name
                        )
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

                        compat_key, resolved_event = (
                            self._streamdeck_http_dispatch_emits(
                                template_name,
                                action_name,
                                event_name,
                                action_data,
                                use_client_event_name=use_client_event,
                            )
                        )

                        logger.debug(
                            "Stream Deck: Template action requested - %s.%s "
                            "(resolved key: %s, event: %s)",
                            template_name,
                            action_name,
                            compat_key,
                            resolved_event,
                        )

                        return (
                            {
                                "success": True,
                                "templateName": template_name,
                                "actionName": compat_key,
                                "eventName": resolved_event,
                                "message": f"Executed {template_name}.{compat_key} (event: {resolved_event})",
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
                                                    action_info[
                                                        "default_data"
                                                    ] = _dynamic_counter_control_default_data(
                                                        element
                                                    )
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
                raw_event_name = data.get("eventName")
                use_client_event = (
                    raw_event_name is not None
                    and str(raw_event_name).strip() != ""
                )
                event_name = (
                    str(raw_event_name).strip()
                    if use_client_event
                    else action_name
                )
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

                compat_key, resolved_event = self._streamdeck_http_dispatch_emits(
                    template_name,
                    action_name,
                    event_name,
                    action_data,
                    use_client_event_name=use_client_event,
                )

                logger.debug(
                    "Stream Deck: Template action requested - %s.%s "
                    "(resolved key: %s, event: %s)",
                    template_name,
                    action_name,
                    compat_key,
                    resolved_event,
                )

                logger.debug(
                    "Stream Deck: Emitted template action events for %s.%s",
                    template_name,
                    compat_key,
                )

                return (
                    {
                        "success": True,
                        "templateName": template_name,
                        "actionName": compat_key,
                        "eventName": resolved_event,
                        "message": f"Executed {template_name}.{compat_key} (event: {resolved_event})",
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
                                            action_info[
                                                "default_data"
                                            ] = _dynamic_counter_control_default_data(
                                                element
                                            )
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

        @self.app.route("/api/streamdeck/get_connectors", methods=["GET"])
        def streamdeck_get_connectors():
            """List enabled connectors with Stream Deck trigger for plugin dropdown."""
            try:
                from .connector_core import TriggerType
                from .connector_manager import get_manager

                manager = get_manager()
                connectors = manager.get_connectors_by_trigger_type(
                    TriggerType.STREAMDECK
                )
                payload = [
                    {
                        "connector_id": c.connector_id,
                        "name": c.name,
                        "description": c.description or "",
                    }
                    for c in connectors
                    if c.enabled
                ]
                payload.sort(key=lambda item: item["name"].lower())
                return (
                    {
                        "success": True,
                        "connectors": payload,
                        "count": len(payload),
                    },
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    f"Stream Deck: Error loading connectors: {e}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "connectors": [],
                        "count": 0,
                        "error": str(e),
                        "message": "Failed to load connectors",
                    },
                    500,
                    {"Content-Type": "application/json"},
                )

        @self.app.route("/api/streamdeck/trigger_connector", methods=["POST"])
        def streamdeck_trigger_connector():
            """Queue a Stream Deck connector trigger (returns after enqueue)."""
            try:
                from .connector_core import TriggerType
                from .connector_manager import get_manager

                data = request.get_json(silent=True) or {}
                connector_id = data.get("connectorId") or data.get("connector_id")
                if not connector_id:
                    return (
                        {
                            "success": False,
                            "message": "connectorId is required",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )

                manager = get_manager()
                connector = manager.get_connector(connector_id)
                if not connector:
                    return (
                        {
                            "success": False,
                            "message": "Connector not found",
                        },
                        404,
                        {"Content-Type": "application/json"},
                    )
                if not connector.enabled:
                    return (
                        {
                            "success": False,
                            "message": "Connector is disabled",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )
                if (
                    not connector.trigger
                    or connector.trigger.trigger_type != TriggerType.STREAMDECK
                ):
                    return (
                        {
                            "success": False,
                            "message": "Connector is not a Stream Deck trigger",
                        },
                        400,
                        {"Content-Type": "application/json"},
                    )

                event_data = {
                    "event_type": "streamdeck",
                    "connector_id": connector_id,
                    "source": "streamdeck",
                    "timestamp": time.time(),
                }
                if not enqueue_streamdeck_connector_event(event_data):
                    return (
                        {
                            "success": False,
                            "message": (
                                "Connector system is not ready yet. "
                                "Wait for Mycelian to finish starting, then try again."
                            ),
                        },
                        503,
                        {"Content-Type": "application/json"},
                    )

                return (
                    {
                        "success": True,
                        "connector_id": connector_id,
                        "message": f"Queued connector '{connector.name}'",
                    },
                    200,
                    {"Content-Type": "application/json"},
                )
            except Exception as e:
                logger.error(
                    f"Stream Deck: Error triggering connector: {e}", exc_info=True
                )
                return (
                    {
                        "success": False,
                        "error": str(e),
                        "message": "Failed to trigger connector",
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

        # Register a catch-all template route AFTER all explicit routes so
        # templates created at runtime (Spore Studio "Create" or files
        # dropped into ``templates/``) become live without an app restart.
        # Flask 3 forbids ``add_url_rule`` after the first request, but
        # this one-time registration during __init__ is safe; subsequent
        # template creations are served by a single fallback that does
        # the disk lookup at request time.
        self._register_template_fallback_route()

        # WebSocket event handlers
        self.register_socket_events()

        # Thread for running the server
        self.server_thread = None
        self.is_running = False

        # Auto-recovery: a native supervisor thread (started by alert_processor)
        # restarts the server when its thread exits or the gevent hub freezes,
        # so OBS browser sources, Stream Deck, and alerts come back without the
        # user having to restart the whole app.
        self._restart_lock = threading.Lock()
        self._last_restart_ts = 0.0
        self._restart_attempts = 0
        self._supervisor_thread: Optional[threading.Thread] = None
        self._restart_giveup_notified = False

    _ALL_TEMPLATE_CONFIGS_CACHE_TTL = 3.0

    def _build_all_template_configs(self, preview_tok: Optional[str]) -> Dict[str, Any]:
        """Load every template JSON (used by cached all-template-configs route)."""
        configs: Dict[str, Any] = {}
        config_parser = self.template_config_parser
        config_files = config_parser.get_config_files()
        for config_name in config_files:
            try:
                config = config_parser.load_config(
                    config_name, include_dynamic_controls=False
                )
                self._merge_preview_session_into_config(
                    preview_tok, config_name, config
                )
                configs[config_name] = config
            except Exception as e:
                logger.warning(
                    "Error loading config for %s: %s", config_name, e
                )
        return configs

    def _get_all_template_configs_cached(
        self, preview_tok: str
    ) -> Tuple[bytes, bool]:
        """Return UTF-8 JSON bytes and whether the response came from TTL cache."""
        cache_key = preview_tok or ""
        now = time.time()
        with self._all_template_configs_cache_lock:
            cached = self._all_template_configs_cache
            if cached is not None:
                key, expires_at, payload_bytes = cached
                if key == cache_key and now < expires_at:
                    return payload_bytes, True
        configs = self._build_all_template_configs(preview_tok or None)
        payload_bytes = json.dumps(configs, separators=(",", ":")).encode("utf-8")
        with self._all_template_configs_cache_lock:
            self._all_template_configs_cache = (
                cache_key,
                now + self._ALL_TEMPLATE_CONFIGS_CACHE_TTL,
                payload_bytes,
            )
        return payload_bytes, False

    def invalidate_all_template_configs_cache(self) -> None:
        with self._all_template_configs_cache_lock:
            self._all_template_configs_cache = None

    def broadcast_template_config_updated(self, template_name: str) -> None:
        """Notify overlays that a template JSON was saved (reload via single-config API)."""
        if not template_name:
            return
        try:
            with self.app.app_context():
                self.socketio.emit(
                    "template_config_updated",
                    {"template": str(template_name)},
                )
        except Exception as e:
            logger.debug(
                "broadcast_template_config_updated failed for %s: %s",
                template_name,
                e,
            )

    def register_assets_watch_template(self, template_name: str) -> None:
        """Limit Spore assets mtime polling to active preview/editor templates."""
        name = (template_name or "").strip()
        if not name:
            return
        with self._assets_watch_templates_lock:
            self._assets_watch_templates.add(name)

    def unregister_assets_watch_template(self, template_name: str) -> None:
        name = (template_name or "").strip()
        if not name:
            return
        with self._assets_watch_templates_lock:
            self._assets_watch_templates.discard(name)

    def get_assets_watch_templates(self) -> List[str]:
        with self._assets_watch_templates_lock:
            return sorted(self._assets_watch_templates)

    def _stop_twitch_api_worker(self) -> None:
        """Signal the dedicated Twitch API worker thread to exit."""
        with self._twitch_api_worker_lock:
            if not self._twitch_api_worker_started:
                return
        try:
            self._twitch_api_queue.put(None)
        except Exception as e:
            logger.debug("Could not enqueue Twitch API worker shutdown: %s", e)

    def ensure_twitch_api_worker(self) -> None:
        with self._twitch_api_worker_lock:
            if self._twitch_api_worker_started:
                return
            self._twitch_api_worker_started = True
        threading.Thread(
            target=self._twitch_api_worker_loop,
            name="MycelianTwitchApiWorker",
            daemon=True,
        ).start()

    def _submit_twitch_api_coro(self, coro) -> None:
        self.ensure_twitch_api_worker()
        self._twitch_api_queue.put(coro)

    def _twitch_api_worker_loop(self) -> None:
        try:
            while True:
                coro = self._twitch_api_queue.get()
                if coro is None:
                    break
                try:
                    # Fresh loop per job so aiohttp connector cleanup completes fully.
                    asyncio.run(coro)
                except Exception as e:
                    logger.error(
                        "Twitch API worker job failed: %s", e, exc_info=True
                    )
        finally:
            with self._twitch_api_worker_lock:
                self._twitch_api_worker_started = False

    def _socket_client_connected(self) -> int:
        with self._socket_connected_lock:
            self._socket_connected_count += 1
            return self._socket_connected_count

    def _socket_client_disconnected(self) -> int:
        with self._socket_connected_lock:
            self._socket_connected_count = max(
                0, self._socket_connected_count - 1
            )
            return self._socket_connected_count

    def _get_socket_connected_count(self) -> int:
        with self._socket_connected_lock:
            return self._socket_connected_count

    def ensure_web_engine_heartbeat(self) -> None:
        """Log connected client count periodically and arm the freeze watchdog."""
        with self._heartbeat_lock:
            if self._heartbeat_task_started:
                return
            self._heartbeat_task_started = True
            # Seed the heartbeat now so the watchdog has a baseline.
            self._last_gevent_heartbeat = time.time()
        try:
            self.socketio.start_background_task(self._web_engine_heartbeat_loop)
        except Exception as exc:
            with self._heartbeat_lock:
                self._heartbeat_task_started = False
            logger.warning(
                "Could not start WebEngine heartbeat task: %s", exc, exc_info=True
            )
            return

        # Native (non-greenlet) watchdog thread: keeps running even if the gevent
        # hub is fully blocked, so it can capture the frozen state.
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name="MycelianWebEngineWatchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def _web_engine_heartbeat_loop(self) -> None:
        while self.is_running:
            try:
                self.socketio.sleep(10.0)
            except Exception:
                break
            if not self.is_running:
                break
            # Mark the gevent hub as alive for the watchdog.
            self._last_gevent_heartbeat = time.time()
            logger.info(
                "WebEngine heartbeat: connected_clients=%s is_running=%s",
                self._get_socket_connected_count(),
                self.is_running,
            )

    def _watchdog_loop(self) -> None:
        """Detect a frozen gevent hub and dump thread stacks for diagnosis.

        The gevent heartbeat ticks every ~10s. If it stops advancing, the
        Socket.IO hub thread is blocked (e.g. on a native lock) and OBS sources /
        Stream Deck are effectively dead. We dump every thread's stack once per
        stall so the actual blocking call site is captured in the logs.
        """
        stall_threshold = 45.0  # ~4 missed 10s heartbeats
        poll_interval = 15.0
        dumped = False
        while self.is_running:
            time.sleep(poll_interval)
            if not self.is_running:
                break
            last = self._last_gevent_heartbeat
            if last is None:
                continue
            stalled_for = time.time() - last
            if stalled_for > stall_threshold:
                if not dumped:
                    self._dump_all_thread_stacks(
                        f"WebEngine gevent hub unresponsive for {stalled_for:.0f}s"
                    )
                    dumped = True
            else:
                if dumped:
                    logger.warning(
                        "WebEngine watchdog: gevent hub recovered after stall"
                    )
                dumped = False

    def _dump_all_thread_stacks(self, reason: str) -> None:
        """Write every thread's stack to a freeze-dump file (and the log).

        Uses ``faulthandler`` writing straight to a file descriptor so the dump
        succeeds even if the stall involves the logging lock itself.
        """
        try:
            log_dir = Path(get_data_path("logs"))
            log_dir.mkdir(exist_ok=True)
            dump_path = (
                log_dir / f"freeze_dump_{datetime.now():%Y%m%d_%H%M%S}.txt"
            )
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(
                    f"Thread stack dump ({reason}) at "
                    f"{datetime.now().isoformat()}\n\n"
                )
                faulthandler.dump_traceback(file=fh, all_threads=True)
            logger.error(
                "WebEngine watchdog: %s — wrote thread stack dump to %s",
                reason,
                dump_path,
            )
        except Exception as e:
            logger.error(
                "WebEngine watchdog: failed to write thread dump (%s): %s",
                reason,
                e,
            )

    # --- Auto-recovery (supervisor + restart) --------------------------------

    _RESTART_COOLDOWN_SEC = 30.0
    _SUPERVISOR_POLL_SEC = 15.0
    _FREEZE_RESTART_THRESHOLD_SEC = 60.0
    _MAX_RESTART_ATTEMPTS = 5

    def start_supervisor(self) -> None:
        """Start the native supervisor that keeps the overlay server alive.

        Detects the two failure modes that leave the NiceGUI window working
        while OBS sources, Stream Deck, and alerts go dead: the WebEngine
        thread exiting, and the gevent hub freezing. Both trigger an automatic
        restart attempt.
        """
        if (
            self._supervisor_thread is not None
            and self._supervisor_thread.is_alive()
        ):
            return
        self._supervisor_thread = threading.Thread(
            target=self._supervisor_loop,
            name="WebEngineSupervisor",
            daemon=True,
        )
        self._supervisor_thread.start()
        logger.info("WebEngine supervisor thread started")

    def _supervisor_loop(self) -> None:
        from .shutdown import is_shutdown_in_progress

        # Give the server a moment to bind before judging its health.
        time.sleep(self._SUPERVISOR_POLL_SEC)
        while True:
            if is_shutdown_in_progress():
                return
            try:
                thread_alive = (
                    self.server_thread is not None
                    and self.server_thread.is_alive()
                )
                if not thread_alive and not self.is_running:
                    self.request_restart("WebEngine thread is no longer running")
                else:
                    last = self._last_gevent_heartbeat
                    if (
                        self.is_running
                        and last is not None
                        and (time.time() - last)
                        > self._FREEZE_RESTART_THRESHOLD_SEC
                    ):
                        self.request_restart(
                            "WebEngine gevent hub stopped responding "
                            f"({time.time() - last:.0f}s without heartbeat)"
                        )
            except Exception as e:
                logger.debug("WebEngine supervisor check failed: %s", e)
            time.sleep(self._SUPERVISOR_POLL_SEC)

    def request_restart(self, reason: str) -> None:
        """Schedule a best-effort server restart (cooldown- and limit-gated)."""
        from .shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            return
        with self._restart_lock:
            now = time.time()
            if now - self._last_restart_ts < self._RESTART_COOLDOWN_SEC:
                return
            if self._restart_attempts >= self._MAX_RESTART_ATTEMPTS:
                if not self._restart_giveup_notified:
                    self._restart_giveup_notified = True
                    self._notify_restart_giveup()
                return
            self._last_restart_ts = now
            self._restart_attempts += 1
            attempt = self._restart_attempts
        threading.Thread(
            target=self._do_restart,
            args=(reason, attempt),
            name="WebEngineRestart",
            daemon=True,
        ).start()

    def _do_restart(self, reason: str, attempt: int) -> None:
        logger.error("WebEngine auto-restart (attempt %s): %s", attempt, reason)
        self._notify_restart_attempt()

        # Best-effort stop of the existing (possibly frozen) server.
        try:
            self.stop()
        except Exception as e:
            logger.warning("WebEngine restart: stop() failed: %s", e)

        old_thread = self.server_thread
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=8.0)

        # Re-arm the lazily-started gevent workers so they restart when a
        # client (e.g. an OBS browser source) reconnects.
        with self._heartbeat_lock:
            self._heartbeat_task_started = False
        with self._template_control_emit_worker_lock:
            self._template_control_emit_worker_started = False
        self._last_gevent_heartbeat = None

        # Start a fresh server thread. This fails to bind if the old hub is
        # frozen and still holding the port; that case is handled below.
        try:
            self._start_server_thread()
        except Exception as e:
            logger.error("WebEngine restart: failed to start new thread: %s", e)

        time.sleep(3.0)
        if self.is_alive():
            logger.warning(
                "WebEngine auto-restart succeeded (attempt %s)", attempt
            )
            with self._restart_lock:
                self._restart_attempts = 0
                self._restart_giveup_notified = False
            self._notify_restart_recovered()
        else:
            logger.error(
                "WebEngine auto-restart did not recover (attempt %s)", attempt
            )
            with self._restart_lock:
                give_up = (
                    self._restart_attempts >= self._MAX_RESTART_ATTEMPTS
                )
            if give_up and not self._restart_giveup_notified:
                self._restart_giveup_notified = True
                self._notify_restart_giveup()

    def _start_server_thread(self) -> None:
        global web_engine_instance
        self.server_thread = threading.Thread(
            target=self.run, name="WebEngine", daemon=True
        )
        self.server_thread.start()
        web_engine_instance = self

    def _notify_restart_attempt(self) -> None:
        try:
            from .notification_engine import notify_critical

            notify_critical(
                "The overlay server (OBS browser sources, Stream Deck, and "
                "alerts) stopped responding and is being restarted "
                "automatically.",
                dedupe_key="web_engine:restart_attempt",
                dedupe_cooldown_sec=60.0,
            )
        except Exception as e:
            logger.debug("WebEngine restart notification failed: %s", e)

    def _notify_restart_recovered(self) -> None:
        try:
            from .notification_engine import notify

            notify(
                "The overlay server recovered. OBS sources, Stream Deck, and "
                "alerts should be working again.",
                type="positive",
                dedupe_key="web_engine:restart_recovered",
                dedupe_cooldown_sec=60.0,
            )
        except Exception as e:
            logger.debug("WebEngine recovery notification failed: %s", e)

    def _notify_restart_giveup(self) -> None:
        try:
            from .notification_engine import notify_critical

            notify_critical(
                "The overlay server stopped responding and could not be "
                "restarted automatically. Please restart Mycelian to restore "
                "OBS sources, Stream Deck, and alerts.",
                dedupe_key="web_engine:restart_giveup",
                dedupe_cooldown_sec=300.0,
            )
        except Exception as e:
            logger.debug("WebEngine give-up notification failed: %s", e)

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
                        preview_token = request.args.get("__preview_token")
                        mycelian_preview_mode = False
                        overrides: Dict[str, Any] = {}
                        sess: Optional[Dict[str, Any]] = None
                        if preview_token:
                            with engine_self._preview_sessions_lock:
                                sess = engine_self._preview_sessions.get(preview_token)
                            if (
                                isinstance(sess, dict)
                                and sess.get("template") == template
                            ):
                                ov = sess.get("overrides")
                                if isinstance(ov, dict):
                                    overrides = ov
                                mycelian_preview_mode = True

                        draft_html: Optional[str] = None
                        draft_config: Optional[Dict[str, Any]] = None
                        if isinstance(sess, dict):
                            dh = sess.get("draft_html")
                            dc = sess.get("draft_config")
                            if (
                                isinstance(dh, str)
                                and dh.strip()
                                and isinstance(dc, dict)
                            ):
                                draft_html = dh
                                draft_config = dc

                        if draft_html is not None and draft_config is not None:
                            template_config = copy.deepcopy(draft_config)
                        elif overrides or mycelian_preview_mode:
                            template_config = copy.deepcopy(
                                engine_self.template_config_parser.load_config(
                                    template
                                )
                            )
                        else:
                            template_config = (
                                engine_self.template_config_parser.load_config(
                                    template
                                )
                            )

                        engine_self._apply_preview_config_layers(
                            template_config,
                            overrides,
                            preview_mode=mycelian_preview_mode,
                        )

                        template_vars = WebEngine._template_variable_map(
                            template_config
                        )

                        logger.debug(
                            f"Template {template} variables: {list(template_vars.keys())}"
                        )
                        if draft_html is not None:
                            html = render_template_string(
                                draft_html,
                                **template_vars,
                                mycelian_html_stem=str(template),
                                mycelian_preview_mode=mycelian_preview_mode,
                            )
                        else:
                            html = render_template(
                                f"{template}.html",
                                **template_vars,
                                mycelian_html_stem=str(template),
                                mycelian_preview_mode=mycelian_preview_mode,
                            )
                        if mycelian_preview_mode and preview_token:
                            # Inject preview helper (force-show + mock-data
                            # MutationObserver). Try </body> first, then
                            # fall back to appending so malformed templates
                            # still get the helper.
                            if "</body>" in html:
                                html = html.replace(
                                    "</body>",
                                    MYCELIAN_PREVIEW_HELPER_HTML + "</body>",
                                    1,
                                )
                            else:
                                html = html + MYCELIAN_PREVIEW_HELPER_HTML
                            resp = make_response(html)
                            resp.set_cookie(
                                "mycelian_preview_token",
                                preview_token,
                                path="/",
                                samesite="Lax",
                                httponly=False,
                            )
                            return resp
                        return html
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

        except AssertionError as e:
            # Flask 3.x forbids add_url_rule after the first request. The
            # template fallback registered in __init__ handles runtime-
            # created templates instead; downgrade this from error to
            # debug so the log isn't spammed on every save.
            logger.debug(
                "Skipping explicit route for template %s (post-startup); "
                "fallback route will serve it: %s",
                template_name, e,
            )
        except Exception as e:
            logger.error(
                f"Error registering route for template {template_name}: {str(e)}",
                exc_info=True,
            )

    def _register_template_fallback_route(self):
        """Register a one-time catch-all route for runtime-created templates.

        Flask 3.x forbids ``add_url_rule`` after the first request. Without
        this fallback, templates created at runtime (e.g. through Spore
        Studio's Create dialog) would 404 until the user restarted the
        app. The single ``/<__spore_template>`` rule is registered now,
        during ``__init__``, and resolves the template file from disk on
        every request — so any new ``templates/{name}.html`` is served
        immediately.

        Specific routes registered by :func:`_register_template_route`
        always win over this catch-all (Flask matches exact rules before
        variable rules), so existing behavior is unchanged.
        """
        engine_self = self

        def template_fallback(spore_template_name):
            template_name = spore_template_name
            if not template_name or "/" in template_name:
                return ("Not found", 404)
            html_path = os.path.join(self.template_dir, f"{template_name}.html")
            if not os.path.isfile(html_path):
                return ("Not found", 404)
            try:
                preview_token = request.args.get("__preview_token")
                mycelian_preview_mode = False
                overrides: Dict[str, Any] = {}
                sess: Optional[Dict[str, Any]] = None
                if preview_token:
                    with engine_self._preview_sessions_lock:
                        sess = engine_self._preview_sessions.get(preview_token)
                    if (
                        isinstance(sess, dict)
                        and sess.get("template") == template_name
                    ):
                        ov = sess.get("overrides")
                        if isinstance(ov, dict):
                            overrides = ov
                        mycelian_preview_mode = True

                draft_html: Optional[str] = None
                draft_config: Optional[Dict[str, Any]] = None
                if isinstance(sess, dict):
                    dh = sess.get("draft_html")
                    dc = sess.get("draft_config")
                    if (
                        isinstance(dh, str)
                        and dh.strip()
                        and isinstance(dc, dict)
                    ):
                        draft_html = dh
                        draft_config = dc

                if draft_html is not None and draft_config is not None:
                    template_config = copy.deepcopy(draft_config)
                elif overrides or mycelian_preview_mode:
                    template_config = copy.deepcopy(
                        engine_self.template_config_parser.load_config(
                            template_name
                        )
                    )
                else:
                    template_config = (
                        engine_self.template_config_parser.load_config(
                            template_name
                        )
                    )

                engine_self._apply_preview_config_layers(
                    template_config,
                    overrides,
                    preview_mode=mycelian_preview_mode,
                )

                template_vars = WebEngine._template_variable_map(template_config)

                if draft_html is not None:
                    html = render_template_string(
                        draft_html,
                        **template_vars,
                        mycelian_html_stem=str(template_name),
                        mycelian_preview_mode=mycelian_preview_mode,
                    )
                else:
                    html = render_template(
                        f"{template_name}.html",
                        **template_vars,
                        mycelian_html_stem=str(template_name),
                        mycelian_preview_mode=mycelian_preview_mode,
                    )
                if mycelian_preview_mode and preview_token:
                    if "</body>" in html:
                        html = html.replace(
                            "</body>",
                            MYCELIAN_PREVIEW_HELPER_HTML + "</body>",
                            1,
                        )
                    else:
                        html = html + MYCELIAN_PREVIEW_HELPER_HTML
                    resp = make_response(html)
                    resp.set_cookie(
                        "mycelian_preview_token",
                        preview_token,
                        path="/",
                        samesite="Lax",
                        httponly=False,
                    )
                    return resp
                return html
            except Exception as e:
                logger.error(
                    "Template fallback render error for %s: %s",
                    template_name, e, exc_info=True,
                )
                return (f"Error loading template {template_name}: {e}", 500)

        try:
            self.app.add_url_rule(
                "/<spore_template_name>",
                "template_fallback",
                template_fallback,
                methods=["GET"],
            )
        except AssertionError as e:
            logger.warning(
                "Template fallback registration skipped (post-startup): %s", e
            )

    def _create_template_assets_folder(self, template_name):
        """Create assets folder for a template if it doesn't exist"""
        try:
            # Resolve the assets root through path_utils so dev and frozen
            # builds agree on a single location (matches the rest of the
            # codebase). Using os.path.abspath("assets") here would diverge
            # under PyInstaller because cwd != exe_dir.
            assets_dir = get_assets_path()
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

    def emit_preview_mock(
        self,
        token: str,
        event_name: str,
        custom: Optional[Any] = None,
        *,
        alert_type: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Emit one mock Socket.IO event to the preview iframe registered for
        ``token`` (Custom Sources or Spore Studio). Shared by the HTTP emit
        route and the NiceGUI toolbar.

        Returns:
            ``(ok, error_message, emitted_event_name)``.
        """
        sid = self._preview_iframe_sids.get(str(token))
        if not sid:
            return (
                False,
                "No preview iframe registered for that token (open preview first).",
                None,
            )
        try:
            if custom is not None:
                self.socketio.emit(str(event_name), custom, to=sid)
                return True, None, str(event_name)
            from .spore_studio import preview_mocks as _pm

            spec = _pm.build_mock_payload(
                str(event_name),
                alert_type=str(alert_type).strip() if alert_type else None,
            )
            if spec is None:
                return False, f"No mock payload defined for '{event_name}'.", None
            socket_event, body = spec
            self.socketio.emit(socket_event, body, to=sid)
            return True, None, str(socket_event)
        except Exception as e:
            logger.error("emit_preview_mock failed: %s", e, exc_info=True)
            return False, str(e), None

    def emit_preview_settings_to_iframe(self, token: str) -> None:
        """Push persisted preview-only settings (sounds, toolbar) to one iframe."""
        sid = self._preview_iframe_sids.get(str(token))
        if not sid:
            return
        try:
            from .template_preview_settings import load_template_preview_settings

            self.socketio.emit(
                "mycelian_preview_settings",
                load_template_preview_settings(),
                to=sid,
            )
        except Exception as e:
            logger.debug("emit_preview_settings_to_iframe skipped: %s", e)

    def emit_preview_config_refresh(self, token: str) -> None:
        """Ask a preview iframe to reload config via ``loadTemplateConfig`` if present."""
        sid = self._preview_iframe_sids.get(str(token))
        if not sid:
            return
        try:
            self.socketio.emit("mycelian_preview_config_refresh", {}, to=sid)
        except Exception as e:
            logger.debug("emit_preview_config_refresh skipped: %s", e)

    def _register_spore_preview_session_keep_draft(
        self, token: str, template_name: str
    ) -> None:
        """
        Create or refresh Spore preview session without discarding compiled draft HTML.

        Custom Sources continues to use :meth:`push_preview_overrides`, which
        replaces the whole session — intentional: no Spore drafts there.
        """
        if not token or not template_name:
            return
        with self._preview_sessions_lock:
            prev = self._preview_sessions.get(str(token))
            overrides: Dict[str, Any] = {}
            draft_html = None
            draft_config = None
            if isinstance(prev, dict):
                ov = prev.get("overrides")
                if isinstance(ov, dict):
                    overrides = ov
                if prev.get("template") == str(template_name):
                    dh = prev.get("draft_html")
                    dc = prev.get("draft_config")
                    if (
                        isinstance(dh, str)
                        and dh.strip()
                        and isinstance(dc, dict)
                    ):
                        draft_html = dh
                        draft_config = dc
            self._preview_sessions[str(token)] = {
                "template": str(template_name),
                "overrides": overrides,
                "draft_html": draft_html,
                "draft_config": draft_config,
                "ts": time.time(),
            }

    @staticmethod
    def _apply_preview_config_layers(
        template_config: Dict[str, Any],
        overrides: Dict[str, Any],
        *,
        preview_mode: bool,
    ) -> None:
        """Apply Custom Sources overrides and preview mock_values to config."""
        if overrides:
            for element in template_config.get("elements", []):
                if not isinstance(element, dict):
                    continue
                eid = element.get("id")
                if eid in overrides:
                    element["value"] = overrides[eid]
        if preview_mode:
            preview_state = template_config.get("previewState")
            if isinstance(preview_state, dict):
                mock_values = preview_state.get("mock_values")
                if isinstance(mock_values, dict):
                    for element in template_config.get("elements", []):
                        if not isinstance(element, dict):
                            continue
                        eid = element.get("id")
                        if (
                            eid in mock_values
                            and eid not in overrides
                        ):
                            element["value"] = mock_values[eid]

    @staticmethod
    def _color_with_opacity(color: Any, opacity: Any) -> str:
        """Return *color* as rgba with alpha multiplied by *opacity* (0–1)."""
        try:
            opacity_f = max(0.0, min(1.0, float(opacity)))
        except (TypeError, ValueError):
            opacity_f = 1.0
        if opacity_f >= 1.0:
            return str(color) if color is not None else "transparent"
        if opacity_f <= 0.0:
            return "transparent"
        c = str(color or "").strip()
        if not c or c.lower() == "transparent":
            return "transparent"
        hex_m = re.match(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", c, re.I)
        if hex_m:
            h = hex_m.group(1)
            if len(h) == 3:
                h = "".join(ch * 2 for ch in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{opacity_f})"
        rgb_m = re.match(
            r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$",
            c,
            re.I,
        )
        if rgb_m:
            a = float(rgb_m.group(4)) if rgb_m.group(4) is not None else 1.0
            return (
                f"rgba({rgb_m.group(1)},{rgb_m.group(2)},"
                f"{rgb_m.group(3)},{a * opacity_f})"
            )
        return c

    @staticmethod
    def _template_variable_map(template_config: Dict[str, Any]) -> Dict[str, Any]:
        from .spore_studio.fonts_registry import resolve_font_filename

        out: Dict[str, Any] = {}
        for element in template_config.get("elements", []):
            if (
                isinstance(element, dict)
                and "id" in element
                and "value" in element
            ):
                eid = str(element["id"])
                val = element["value"]
                if eid.endswith("_font_family") and val not in (None, ""):
                    resolved = resolve_font_filename(str(val))
                    if resolved:
                        val = resolved
                out[eid] = val
        if "BGColor" in out and "BGOpacity" in out:
            out["BGColorWithOpacity"] = WebEngine._color_with_opacity(
                out["BGColor"], out["BGOpacity"]
            )
        return out

    def _merge_preview_session_into_config(
        self,
        token: Optional[str],
        config_name: str,
        config: Dict[str, Any],
    ) -> None:
        """Merge pending preview overrides into ``config`` when cookie/token matches."""
        if not token or not isinstance(config, dict):
            return
        with self._preview_sessions_lock:
            sess = self._preview_sessions.get(token)
        if not isinstance(sess, dict) or sess.get("template") != config_name:
            return
        overrides = sess.get("overrides")
        if not isinstance(overrides, dict):
            return
        for element in config.get("elements", []):
            if not isinstance(element, dict):
                continue
            eid = element.get("id")
            if eid in overrides:
                element["value"] = overrides[eid]

    def _maybe_start_preview_demo(self, sid: str) -> None:
        """
        Hook fired when a preview iframe (Custom Sources OR Spore Studio)
        connects. The continuous auto-demo loop has been removed; this
        hook now only exists to register the ``token -> sid`` mapping
        used by the Spore Studio manual mock-event endpoint
        (``/api/spore-studio/preview/emit``).
        """
        try:
            token = request.cookies.get(
                "mycelian_preview_token"
            ) or request.args.get("__preview_token")
            if not token:
                return
            self._preview_iframe_sids[token] = sid
            self._preview_iframe_tokens[sid] = token
        except Exception as e:
            logger.warning("preview iframe register failed: %s", e, exc_info=True)

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
            connected = self._socket_client_connected()
            logger.info(
                "Socket.IO client connected sid=%s (connected=%s)",
                request.sid,
                connected,
            )

            try:
                self.ensure_web_engine_heartbeat()
            except Exception as e:
                logger.debug("WebEngine heartbeat hook failed: %s", e)

            try:
                from .spore_studio import assets_watcher as _ss_aw

                _ss_aw.ensure_background_poller(self.socketio)
            except Exception as e:
                logger.debug("Spore Studio assets poller hook failed: %s", e)

            try:
                self.ensure_template_control_emit_worker()
            except Exception as e:
                logger.debug("template control emit worker hook failed: %s", e)

            try:
                self._maybe_start_preview_demo(request.sid)
            except Exception as e:
                logger.warning(
                    "preview iframe sid registration failed for %s: %s",
                    request.sid,
                    e,
                    exc_info=True,
                )

            connect_sid = request.sid

            def deferred_initial_client_broadcast() -> None:
                """Avoid spamming preview iframes with live pause/theme payloads."""
                try:
                    self.socketio.sleep(0.12)
                    if connect_sid in self._preview_iframe_tokens:
                        return
                    global ALERTS_PAUSED, ALERTS_MUTED
                    logger.debug(
                        "Sending initial pause status to %s: paused=%s",
                        connect_sid,
                        ALERTS_PAUSED,
                    )
                    self.socketio.emit(
                        "pause_status_update",
                        {"paused": ALERTS_PAUSED},
                        to=connect_sid,
                    )
                    if ALERTS_PAUSED:
                        self.socketio.emit(
                            "alerts_paused", {"paused": True}, to=connect_sid
                        )
                    else:
                        self.socketio.emit(
                            "alerts_resumed", {"paused": False}, to=connect_sid
                        )
                    logger.debug(
                        "Sending initial mute status to %s: muted=%s",
                        connect_sid,
                        ALERTS_MUTED,
                    )
                    self.socketio.emit(
                        "mute_status_update",
                        {"muted": ALERTS_MUTED},
                        to=connect_sid,
                    )
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
                            to=connect_sid,
                        )
                        logger.debug(
                            "Sent initial theme to %s: %s",
                            connect_sid,
                            theme.name,
                        )
                except Exception as e:
                    logger.error(
                        "Error in deferred connect broadcast for %s: %s",
                        connect_sid,
                        str(e),
                        exc_info=True,
                    )

            try:
                self.socketio.start_background_task(deferred_initial_client_broadcast)
            except Exception as e:
                logger.warning(
                    "deferred connect broadcast scheduling failed: %s",
                    e,
                    exc_info=True,
                )

        @self.socketio.on("mycelian_preview_register")
        def handle_mycelian_preview_register(data=None):
            """Bind preview iframe sid to token (cookie-free fallback)."""
            try:
                tok = None
                if isinstance(data, dict):
                    tok = data.get("token")
                if not tok:
                    return
                tok = str(tok)
                self._preview_iframe_sids[tok] = request.sid
                self._preview_iframe_tokens[request.sid] = tok
                with self._preview_sessions_lock:
                    sess = self._preview_sessions.get(tok)
                if isinstance(sess, dict) and sess.get("template"):
                    self.register_assets_watch_template(str(sess["template"]))
                from .template_preview_settings import load_template_preview_settings

                self.socketio.emit(
                    "mycelian_preview_settings",
                    load_template_preview_settings(),
                    to=request.sid,
                )
            except Exception as e:
                logger.debug("mycelian_preview_register failed: %s", e)

        @self.socketio.on("mycelian_preview_client_ready")
        def handle_mycelian_preview_client_ready():
            """Preview iframe registered hooks; push persisted preview-only settings."""
            try:
                token = self._preview_iframe_tokens.get(request.sid)
                if not token:
                    return
                from .template_preview_settings import load_template_preview_settings

                self.socketio.emit(
                    "mycelian_preview_settings",
                    load_template_preview_settings(),
                    to=request.sid,
                )
            except Exception as e:
                logger.debug("mycelian_preview_client_ready failed: %s", e)

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
                    logger.debug(f"Sent current theme to {request.sid}: {theme.name}")
            except Exception as e:
                logger.error(
                    f"Error handling get_current_theme: {str(e)}",
                    exc_info=True,
                )

        @self.socketio.on("disconnect")
        def handle_disconnect():
            connected = self._socket_client_disconnected()
            logger.info(
                "Socket.IO client disconnected sid=%s (connected=%s)",
                request.sid,
                connected,
            )
            self._preview_demo_stop[request.sid] = True
            stale_token = self._preview_iframe_tokens.pop(
                request.sid, None
            )
            if stale_token is not None:
                # Only drop the token mapping when it still points at
                # this sid — a stale entry could otherwise nuke a fresh
                # iframe that re-registered the same token.
                if self._preview_iframe_sids.get(stale_token) == request.sid:
                    self._preview_iframe_sids.pop(stale_token, None)

        @self.socketio.on("game_hook_command")
        def handle_game_hook_command(data):
            """Template → server commands for game hooks (e.g. clear boss list)."""
            try:
                from .game_hooks_service import (
                    handle_game_hook_command as _run_hook_cmd,
                )

                _run_hook_cmd(data)
            except Exception as e:
                logger.error(
                    f"Error handling game_hook_command: {str(e)}", exc_info=True
                )

        @self.socketio.on("alert_complete")
        def handle_alert_complete(data=None):
            global ALERT_PLAYING, EXPECTED_ALERT_COMPLETE_SEQ
            seq = None
            if isinstance(data, dict):
                seq = data.get("queue_seq")
            try:
                seq = int(seq)
            except (TypeError, ValueError):
                logger.debug(
                    "alert_complete ignored (missing or invalid queue_seq): %s", data
                )
                return
            if EXPECTED_ALERT_COMPLETE_SEQ is None:
                logger.debug(
                    "alert_complete ignored (no active expected queue_seq)"
                )
                return
            if seq != EXPECTED_ALERT_COMPLETE_SEQ:
                logger.debug(
                    "alert_complete ignored (seq=%s, expected=%s)",
                    seq,
                    EXPECTED_ALERT_COMPLETE_SEQ,
                )
                return
            ALERT_PLAYING = False
            logger.debug(
                "Alert completed for queue_seq=%s, ALERT_PLAYING set to False",
                seq,
            )

        @self.socketio.on("pause_alerts")
        def handle_pause_alerts():
            # Delegate to the main toggle_alerts method for consistency
            self.toggle_alerts()

        @self.socketio.on("resume_alerts")
        def handle_resume_alerts():
            global ALERTS_PAUSED
            if ALERTS_PAUSED:
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

                if path == "statistics/session":
                    try:
                        sm = statistics_manager.get_statistics_manager()
                        alerts = sm.data.alerts
                        session_doc = {
                            "total_gift_subs": int(alerts.total_gift_subs or 0),
                            "total_bits": int(alerts.total_bits or 0),
                            "follows": int(alerts.follow_alerts_played or 0),
                            "subs": int(alerts.new_subs_played or 0)
                            + int(alerts.resubs_played or 0),
                            "raids": int(alerts.raids or 0),
                            "cheers": int(alerts.bit_alerts_played or 0),
                        }
                        result = {"data": session_doc}
                        self.socketio.emit("get_data", result, to=request.sid)
                        return result
                    except Exception as exc:
                        logger.debug(
                            "statistics/session snapshot failed: %s", exc
                        )

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

                        from .dataobjects import state_manager

                        twitch_data = state_manager.get_twitch_data()

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

                        if (
                            twitch.twitch_api.auth_token != twitch_data.auth_token
                            or twitch.twitch_api.client_id != twitch_data.client_id
                            or twitch.twitch_api.client_secret
                            != twitch_data.client_secret
                            or twitch.twitch_api.refresh_token
                            != twitch_data.refresh_token
                        ):
                            logger.debug(
                                "Updating Twitch API instance with current state manager data for proxy call"
                            )
                            twitch.twitch_api.auth_token = twitch_data.auth_token
                            twitch.twitch_api.client_id = twitch_data.client_id
                            twitch.twitch_api.client_secret = twitch_data.client_secret
                            twitch.twitch_api.refresh_token = twitch_data.refresh_token
                            if twitch_data.token_expiry:
                                try:
                                    twitch.twitch_api.token_expiry = (
                                        datetime.fromisoformat(
                                            twitch_data.token_expiry
                                        )
                                    )
                                except ValueError:
                                    logger.warning(
                                        "Invalid token expiry format in state manager: %s",
                                        twitch_data.token_expiry,
                                    )
                                    twitch.twitch_api.token_expiry = None

                        original_auth_token = twitch.twitch_api.auth_token
                        original_refresh_token = twitch.twitch_api.refresh_token

                        api_response = await twitch.twitch_api.generic_api_call(
                            url=url,
                            method=method,
                            params=params,
                            json_data=json_payload,
                        )

                        if (
                            twitch.twitch_api.auth_token != original_auth_token
                            or twitch.twitch_api.refresh_token
                            != original_refresh_token
                        ):
                            logger.info(
                                "Tokens were refreshed during API proxy call - syncing to state manager"
                            )
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
                                    "current_category": twitch_data.current_category,
                                }
                                state_manager.set_twitch_data(refreshed_twitch_data)
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
                                    "Error syncing refreshed tokens from API proxy call: %s",
                                    sync_error,
                                    exc_info=True,
                                )

                        logger.debug(
                            "Twitch API proxy call successful for URL: %s", url
                        )
                        self.socketio.emit(
                            "twitch_api_proxy_response",
                            {"success": True, "data": api_response},
                            to=client_sid,
                        )

                    except Exception as e:
                        logger.error(
                            "Error in async twitch_api_proxy handler: %s",
                            e,
                            exc_info=True,
                        )
                        self.socketio.emit(
                            "twitch_api_proxy_response",
                            {"error": str(e), "success": False},
                            to=client_sid,
                        )

                self._submit_twitch_api_coro(handle_request())

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

        @self.socketio.on("get_mute_status")
        def handle_get_mute_status():
            """Return the current mute status to the requesting client."""
            client_sid = request.sid
            global ALERTS_MUTED
            logger.debug(f"Received get_mute_status request from {client_sid}")
            self.socketio.emit(
                "mute_status_update", {"muted": ALERTS_MUTED}, to=client_sid
            )
            return {"muted": ALERTS_MUTED}

        @self.socketio.on("set_mute_status")
        def handle_set_mute_status(data):
            """Allow clients to set the alert audio mute status."""
            global ALERTS_MUTED
            try:
                if not isinstance(data, dict) or "muted" not in data:
                    logger.error(
                        "Invalid data format received in set_mute_status event"
                    )
                    return {
                        "success": False,
                        "error": "Invalid data format: muted boolean required",
                    }

                new_status = bool(data["muted"])
                old_status = ALERTS_MUTED
                ALERTS_MUTED = new_status
                logger.debug(
                    f"Mute status changed from {old_status} to {new_status} by {request.sid}"
                )

                self.socketio.emit("mute_status_update", {"muted": new_status})
                _sync_mute_button_state()
                return {"success": True, "muted": new_status}
            except Exception as e:
                logger.error(f"Error setting mute status: {str(e)}", exc_info=True)
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

                from . import dataobjects

                if alertutils.alert_state_manager is None:
                    logger.warning(
                        "get_stored_alerts_paginated: alert_state_manager not initialized"
                    )
                    result = {
                        "alerts": [],
                        "total_count": 0,
                        "page": page,
                        "limit": limit,
                        "total_pages": 1,
                        "has_next": False,
                        "has_prev": False,
                    }
                else:
                    app_settings = dataobjects.state_manager.get_app_settings()
                    max_total_alerts = (
                        app_settings.activity_feed_max_pages
                        * app_settings.activity_feed_limit
                    )
                    result = alertutils.alert_state_manager.get_limited_stored_alerts_paginated(
                        page=page,
                        limit=limit,
                        max_total_alerts=max_total_alerts,
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

                from modules.uiwindows.activity_feed import (
                    load_restored_alerts_for_time_window,
                )

                current_time = time.time()
                cutoff_time = current_time - (hours * 3600)

                logger.debug(
                    f"Loading condensed view alerts for past {hours} hours (cutoff: {cutoff_time})"
                )

                alerts_to_process, historical_count = (
                    load_restored_alerts_for_time_window(cutoff_time)
                )

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
                    "game_name",
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
            Proxies Twitch Helix requests for templates.

            Payload:
                - endpoint (str): Full URL, must be https://api.twitch.tv/...
                - method (str, optional): HTTP method (default GET).
                - requestId (str): Client correlation id for twitch-api-response.
                - params (dict, optional): URL query parameters.
                - json_data or json (dict, optional): JSON body for POST/PATCH/PUT.
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

                request_id = data["requestId"]
                endpoint = str(data["endpoint"]).strip()
                method = data.get("method", "GET").upper()

                try:
                    parsed = urlparse(endpoint)
                except Exception:
                    parsed = None
                allowed_host = (
                    parsed
                    and parsed.scheme.lower() == "https"
                    and (parsed.hostname or "").lower() == "api.twitch.tv"
                )
                if not allowed_host:
                    self.socketio.emit(
                        "twitch-api-response",
                        {
                            "success": False,
                            "error": "endpoint must be https://api.twitch.tv/…",
                            "requestId": request_id,
                        },
                        to=client_sid,
                    )
                    return

                params = data.get("params")
                if params is not None and not isinstance(params, dict):
                    self.socketio.emit(
                        "twitch-api-response",
                        {
                            "success": False,
                            "error": "params must be an object when provided",
                            "requestId": request_id,
                        },
                        to=client_sid,
                    )
                    return
                json_data = data.get("json_data")
                if json_data is None:
                    json_data = data.get("json")
                if json_data is not None and not isinstance(json_data, dict):
                    self.socketio.emit(
                        "twitch-api-response",
                        {
                            "success": False,
                            "error": "json_data (or json) must be an object when provided",
                            "requestId": request_id,
                        },
                        to=client_sid,
                    )
                    return
                if not twitch.twitch_api:
                    logger.debug(
                        "Twitch API not initialized yet for API request "
                        "(requestId=%s)",
                        request_id,
                    )
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
                        or twitch.twitch_api.client_secret != twitch_data.client_secret
                        or twitch.twitch_api.refresh_token != twitch_data.refresh_token
                    ):
                        logger.debug(
                            "Updating Twitch API instance with current state manager data"
                        )
                        twitch.twitch_api.auth_token = twitch_data.auth_token
                        twitch.twitch_api.client_id = twitch_data.client_id
                        twitch.twitch_api.client_secret = twitch_data.client_secret
                        twitch.twitch_api.refresh_token = twitch_data.refresh_token
                        if twitch_data.token_expiry:
                            try:
                                twitch.twitch_api.token_expiry = (
                                    datetime.fromisoformat(
                                        twitch_data.token_expiry
                                    )
                                )
                            except ValueError:
                                pass

                async def handle_request():
                    try:
                        api_response = await twitch.twitch_api.generic_api_call(
                            url=endpoint,
                            method=method,
                            params=params,
                            json_data=json_data,
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
                            "Twitch API request successful for %s", endpoint
                        )
                    except Exception as e:
                        err = str(e)
                        if err.startswith("Authentication required"):
                            logger.warning(
                                "Twitch API request skipped (%s): %s",
                                endpoint,
                                err,
                            )
                        elif "Network error during API call" in err:
                            try:
                                from .connection_monitor import (
                                    is_internet_available,
                                )

                                offline = not is_internet_available()
                            except Exception:
                                offline = False
                            if offline:
                                logger.warning(
                                    "Twitch API request failed while offline (%s): %s",
                                    endpoint,
                                    err,
                                )
                            else:
                                logger.error(
                                    "Error in async twitch-api-request handler: %s",
                                    e,
                                    exc_info=True,
                                )
                        else:
                            logger.error(
                                "Error in async twitch-api-request handler: %s",
                                e,
                                exc_info=True,
                            )
                        response_data = {
                            "success": False,
                            "error": err,
                            "requestId": request_id,
                        }
                        self.socketio.emit(
                            "twitch-api-response", response_data, to=client_sid
                        )

                self._submit_twitch_api_coro(handle_request())

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
                    err = {"success": False, "error": "Invalid data format"}
                    self.socketio.emit(
                        "streamdeck_action_response",
                        err,
                        to=client_sid,
                    )
                    return

                template_name = data.get("templateName")
                action_name = data.get("actionName")
                coerced = _coerce_streamdeck_action_data(data.get("actionData", {}))

                if not template_name or not action_name:
                    logger.error(
                        "Missing templateName or actionName in streamdeck_template_action"
                    )
                    err = {
                        "success": False,
                        "error": "Missing templateName or actionName",
                    }
                    self.socketio.emit("streamdeck_action_response", err, to=client_sid)
                    if isinstance(data, dict) and data.get("requestId") is not None:
                        self.socketio.emit(
                            "response",
                            {**err, "requestId": data["requestId"]},
                            to=client_sid,
                        )
                    return

                event_name_req = (
                    data.get("eventName", action_name) if isinstance(data, dict) else action_name
                )
                compat_action_key = action_name
                final_broadcast_ad = coerced
                final_broadcast_event = event_name_req

                # Load template configuration
                template_config = self.template_config_parser.load_config(
                    template_name,
                    include_dynamic_controls=True,
                    include_streamdeck_options=True,
                )

                if not template_config:
                    logger.error(f"Template configuration not found: {template_name}")
                    err = {
                        "success": False,
                        "error": f"Template not found: {template_name}",
                    }
                    self.socketio.emit("streamdeck_action_response", err, to=client_sid)
                    if isinstance(data, dict) and data.get("requestId") is not None:
                        self.socketio.emit(
                            "response",
                            {**err, "requestId": data["requestId"]},
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
                    action_config, sd_key = self._resolve_streamdeck_options_action(
                        streamdeck_actions, action_name
                    )
                    if action_config is not None:
                        action_found = True
                        compat_action_key = sd_key or action_name
                        logger.debug(
                            "Found Stream Deck action: %s.%s (key=%s)",
                            template_name,
                            action_name,
                            compat_action_key,
                        )

                        final_broadcast_ad = _merged_streamdeck_options_payload(
                            action_config, coerced
                        )
                        final_broadcast_event = action_config.get(
                            "event", f"{template_name}_{compat_action_key}"
                        )
                        if not isinstance(final_broadcast_event, str):
                            final_broadcast_event = str(final_broadcast_event)

                        # Execute the Stream Deck specific action
                        self._execute_streamdeck_action(
                            template_name,
                            compat_action_key,
                            action_config,
                            coerced,
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
                                        template_name, element, coerced
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
                                coerced,
                            )

                if not action_found:
                    logger.error(
                        "Action not found: %s.%s (streamdeck_options keys: %s)",
                        template_name,
                        action_name,
                        list(
                            (template_config.get("streamdeck_options") or {})
                            .get("actions", {})
                            .keys()
                        )
                        if isinstance(template_config.get("streamdeck_options"), dict)
                        else [],
                    )
                    err = {
                        "success": False,
                        "error": f"Action not found: {action_name}",
                    }
                    self.socketio.emit("streamdeck_action_response", err, to=client_sid)
                    if isinstance(data, dict) and data.get("requestId") is not None:
                        self.socketio.emit(
                            "response",
                            {**err, "requestId": data["requestId"]},
                            to=client_sid,
                        )
                    return

                # Backward-compatible broadcasts (Stream Deck clients / older overlays)
                self.socketio.emit(
                    "streamdeck_template_action",
                    {
                        "templateName": template_name,
                        "actionName": compat_action_key,
                        "eventName": final_broadcast_event,
                        "actionData": final_broadcast_ad,
                    },
                )
                self.socketio.emit(
                    f"{template_name}_{compat_action_key}", final_broadcast_ad
                )
                if final_broadcast_event != compat_action_key:
                    self.socketio.emit(
                        f"{template_name}_{final_broadcast_event}",
                        final_broadcast_ad,
                    )

                # Send success response
                response = {
                    "success": True,
                    "templateName": template_name,
                    "actionName": compat_action_key,
                    "eventName": final_broadcast_event,
                    "message": f"Executed {template_name}.{compat_action_key}",
                }
                self.socketio.emit(
                    "streamdeck_action_response", response, to=client_sid
                )
                if isinstance(data, dict) and data.get("requestId") is not None:
                    self.socketio.emit(
                        "response",
                        {**response, "requestId": data["requestId"]},
                        to=client_sid,
                    )
                logger.info(
                    f"Stream Deck action executed: {template_name}.{compat_action_key}"
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
                if isinstance(data, dict) and data.get("requestId") is not None:
                    self.socketio.emit(
                        "response",
                        {**error_response, "requestId": data["requestId"]},
                        to=client_sid,
                    )

    def _load_streamdeck_template_config(self, template_name: str) -> Dict[str, Any]:
        """Load template config with mtime-keyed cache for Stream Deck presses."""
        config_path = self.template_config_parser.get_config_path(template_name)
        try:
            mtime = (
                os.path.getmtime(config_path)
                if os.path.exists(config_path)
                else 0.0
            )
        except OSError:
            mtime = 0.0
        cached = self._streamdeck_config_cache.get(template_name)
        if cached and cached.get("mtime") == mtime:
            cfg = cached.get("cfg")
            return cfg if isinstance(cfg, dict) else {}
        cfg = self.template_config_parser.load_config(
            template_name,
            include_dynamic_controls=True,
            include_streamdeck_options=True,
        )
        self._streamdeck_config_cache[template_name] = {
            "mtime": mtime,
            "cfg": cfg if isinstance(cfg, dict) else {},
        }
        return self._streamdeck_config_cache[template_name]["cfg"]

    def _emit_streamdeck_template_events(
        self,
        template_name: str,
        compat_key: str,
        resolved_event: str,
        merged_ad: Any,
    ) -> None:
        """Emit primary socket event plus legacy streamdeck_template_action."""
        self.socketio.emit(resolved_event, merged_ad)
        self.socketio.emit(
            "streamdeck_template_action",
            {
                "templateName": template_name,
                "actionName": compat_key,
                "eventName": resolved_event,
                "actionData": merged_ad,
            },
        )
        compat_emit = f"{template_name}_{compat_key}"
        if compat_emit != resolved_event:
            self.socketio.emit(compat_emit, merged_ad)

    def _streamdeck_http_dispatch_emits(
        self,
        template_name: str,
        action_name: str,
        event_name_req: str,
        action_data_raw: Any,
        *,
        use_client_event_name: bool = False,
    ) -> Tuple[str, str]:
        """
        Shared logic for HTTP ``/api/streamdeck/template_action`` and deeplink
        ``template_action``: coerce payload, resolve ``streamdeck_options``,
        emit named event(s) with merged ``actionData``.
        """
        cfg = self._load_streamdeck_template_config(template_name)
        compat_key, resolved_event, merged_ad = plan_streamdeck_template_action_emit(
            template_name=template_name,
            action_name=action_name,
            event_name_req=event_name_req,
            action_data_raw=action_data_raw,
            template_config=cfg if cfg else None,
            use_client_event_name=use_client_event_name,
        )
        self._emit_streamdeck_template_events(
            template_name, compat_key, resolved_event, merged_ad
        )
        logger.info(
            "Stream Deck: dispatched %s.%s (requested=%s, event=%s)",
            template_name,
            compat_key,
            action_name,
            resolved_event,
        )
        return compat_key, resolved_event

    def _resolve_streamdeck_options_action(
        self, streamdeck_actions: dict, action_name: str
    ) -> tuple:
        """Delegate to :func:`streamdeck_template_dispatch.resolve_streamdeck_options_action`."""
        return _resolve_streamdeck_options_action_fn(streamdeck_actions, action_name)

    def _execute_streamdeck_action(
        self,
        template_name: str,
        action_name: str,
        action_config: dict,
        action_data: Any,
    ):
        """Execute a Stream Deck specific action"""
        try:
            event_name = action_config.get("event", f"{template_name}_{action_name}")
            event_data = _merged_streamdeck_options_payload(
                action_config, action_data
            )

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

    def toggle_mute(self):
        global ALERTS_MUTED
        old_status = ALERTS_MUTED
        ALERTS_MUTED = not ALERTS_MUTED

        logger.info(f"Toggling ALERTS_MUTED from {old_status} to {ALERTS_MUTED}")
        self.mute_status_update()

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
                if element.get("target_counter_id") and element.get("action"):
                    actions_to_register.append(element.get("action"))
                else:
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

    def _broadcast_template_control_event(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Emit on the Web Engine Socket.IO stack (same path as source_controls_relay)."""
        with self.app.app_context():
            self.socketio.emit(event_name, event_data)

    def ensure_template_control_emit_worker(self) -> None:
        """Start the gevent worker that drains cross-thread template control emits."""
        with self._template_control_emit_worker_lock:
            if self._template_control_emit_worker_started:
                return
            self._template_control_emit_worker_started = True
        try:
            self.socketio.start_background_task(self._template_control_emit_loop)
        except Exception as exc:
            with self._template_control_emit_worker_lock:
                self._template_control_emit_worker_started = False
            logger.warning(
                "Could not start template control emit worker: %s", exc, exc_info=True
            )

    def _template_control_emit_loop(self) -> None:
        """Drain NiceGUI-thread enqueues and broadcast on the Socket.IO/gevent stack."""
        q = self._template_control_queue
        try:
            while self.is_running:
                batch: List[Tuple[str, Dict[str, Any]]] = []
                try:
                    while True:
                        batch.append(q.get_nowait())
                except queue.Empty:
                    pass

                if batch:
                    # Preserve order across event names; coalesce rapid duplicates (typing).
                    order: List[str] = []
                    latest: Dict[str, Dict[str, Any]] = {}
                    for event_name, event_data in batch:
                        if event_name not in latest:
                            order.append(event_name)
                        latest[event_name] = event_data
                    for event_name in order:
                        event_data = latest[event_name]
                        try:
                            self._broadcast_template_control_event(
                                event_name, event_data
                            )
                        except Exception as e:
                            logger.error(
                                "emit_template_control_event failed for %s: %s",
                                event_name,
                                e,
                                exc_info=True,
                            )

                try:
                    self.socketio.sleep(0.05 if not batch else 0.016)
                except Exception as exc:
                    if not self.is_running:
                        break
                    logger.warning(
                        "template control emit loop sleep failed: %s", exc
                    )
                    try:
                        self.socketio.sleep(0.1)
                    except Exception:
                        break
        finally:
            with self._template_control_emit_worker_lock:
                self._template_control_emit_worker_started = False
            if self.is_running:
                logger.warning(
                    "Template control emit worker exited unexpectedly; restarting"
                )
                try:
                    self.ensure_template_control_emit_worker()
                except Exception as restart_exc:
                    logger.warning(
                        "Could not restart template control emit worker: %s",
                        restart_exc,
                    )

    def emit_template_control_event(
        self,
        template_name: str,
        action: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Broadcast a template control event to all Socket.IO clients (overlays).

        Enqueues from any thread; a Socket.IO background task on the Web Engine
        gevent loop drains the queue (same delivery stack as source_controls_relay).
        """
        if not template_name or not action:
            return
        event_name = f"{template_name}_{action}"
        event_data = data if isinstance(data, dict) else ({} if data is None else {})
        self._template_control_queue.put((event_name, event_data))

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
            self.invalidate_all_template_configs_cache()
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

    def broadcast_overlay_recovery(
        self, service: str = "twitch", reason: str = "reconnected"
    ) -> bool:
        """Tell overlay browser sources to re-sync after a service reconnects."""
        try:
            payload = {"service": service, "reason": reason}
            self.socketio.emit("overlay-recovery", payload)
            logger.info(
                "Broadcast overlay-recovery for %s (%s)", service, reason
            )
            return True
        except Exception as e:
            logger.error(
                "Error broadcasting overlay-recovery: %s", e, exc_info=True
            )
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

    def mute_status_update(self):
        """Broadcast mute status update to all connected clients."""
        try:
            global ALERTS_MUTED

            logger.info(f"Broadcasting mute status update: muted={ALERTS_MUTED}")
            self.socketio.emit("mute_status_update", {"muted": ALERTS_MUTED})
            _sync_mute_button_state()
            logger.info(
                f"Successfully broadcasted mute status update: muted={ALERTS_MUTED}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Error broadcasting mute status update: {str(e)}", exc_info=True
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
                                            action_info[
                                                "default_data"
                                            ] = _dynamic_counter_control_default_data(
                                                element
                                            )
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
        run_error: Optional[BaseException] = None
        try:
            logger.info(
                "Starting WebEngine server on %s:%s", self.host, self.port
            )
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
            run_error = e
            logger.error(f"Error running WebEngine server: {str(e)}", exc_info=True)
            self.is_running = False
            web_engine_running = False
        finally:
            self.is_running = False
            web_engine_running = False
            thread_alive = (
                self.server_thread is not None and self.server_thread.is_alive()
            )
            logger.warning(
                "WebEngine server stopped (host=%s port=%s connected_clients=%s "
                "thread_alive=%s error=%s)",
                self.host,
                self.port,
                self._get_socket_connected_count(),
                thread_alive,
                run_error,
            )

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

            # Spore Studio asset hot-reload poller is scheduled from the first
            # Socket.IO connect (see handle_connect) so emits run on gevent.
        else:
            logger.warning("WebEngine server thread already running")

    def stop(self):
        """Stop the WebEngine server"""
        global web_engine_running
        self._stop_twitch_api_worker()
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
                            # If the underlying WSGI server never finished starting,
                            # flask-socketio's stop() dereferences a None server
                            # ("'NoneType' object has no attribute 'close'"). Skip
                            # the call in that case to avoid the harmless error.
                            wsgi_server = getattr(
                                self.socketio, "wsgi_server", None
                            )
                            server_alive = (
                                self.server_thread is not None
                                and self.server_thread.is_alive()
                            )
                            if wsgi_server is None and not server_alive:
                                logger.debug(
                                    "socketio.stop() skipped: no running WSGI server"
                                )
                            else:
                                self.socketio.stop()
                    except AttributeError as e:
                        # Harmless shutdown race when the server was already torn down.
                        logger.debug("socketio.stop() no-op during shutdown: %s", e)
                    except Exception as e:
                        logger.error(f"Error stopping socketio: {str(e)}")

                stop_thread = threading.Thread(target=stop_server)
                stop_thread.daemon = True
                stop_thread.start()

                # Wait for the stop thread to finish
                stop_thread.join(timeout=2)

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

    _DYNAMIC_CONTROL_ELEMENT_TYPES = frozenset(
        {
            "button",
            "slider",
            "toggle",
            "counter_control",
            "spin_control",
            "text_input",
            "number_input",
        }
    )

    def _get_template_control_count(self, template_name: str) -> int:
        """Count interactive control elements for a template (matches Source Controls tab)."""
        if not getattr(self, "template_config_parser", None):
            return 0
        try:
            config = self.template_config_parser.load_config(
                template_name, include_dynamic_controls=True
            )
            if not isinstance(config, dict):
                return 0
            if "dynamic_controls" in config:
                elements = config["dynamic_controls"].get("elements", [])
                return len(elements) if isinstance(elements, list) else 0
            elements = config.get("elements", [])
            if not isinstance(elements, list):
                return 0
            control_elements = [
                elem
                for elem in elements
                if isinstance(elem, dict)
                and elem.get("type") in self._DYNAMIC_CONTROL_ELEMENT_TYPES
                and (
                    elem.get("action")
                    or elem.get("action_increment")
                    or elem.get("action_decrement")
                    or elem.get("action_reset")
                )
            ]
            return len(control_elements)
        except Exception as e:
            logger.debug(
                "Could not get control count for %s: %s", template_name, e
            )
            return 0

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
                        existing_template["control_count"] = (
                            self._get_template_control_count(template_name)
                        )
                    else:
                        # Template not in registered routes but has dynamic controls
                        # This might happen if the template file was deleted but handlers remain
                        logger.debug(
                            f"Found dynamic handlers for template not in routes: {template_name}"
                        )

            logger.debug(
                f"Found {len(urls)} available source URLs (hidden templates excluded)"
            )
            return urls

        except Exception as e:
            logger.error(
                f"Error getting available source URLs: {str(e)}", exc_info=True
            )
            return []

    def broadcast_theme_update(
        self, theme_css: str, theme_name: str, theme_type: str
    ) -> bool:
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


def broadcast_overlay_recovery(
    service: str = "twitch", reason: str = "reconnected"
) -> bool:
    """Module helper: notify overlay templates that a service recovered."""
    global web_engine_instance
    if web_engine_instance is None:
        return False
    return web_engine_instance.broadcast_overlay_recovery(service, reason)
