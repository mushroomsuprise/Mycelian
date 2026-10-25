# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Which connection-status changes deserve a notification.

The status footer shows every state; notifications only say something when a service
settles into a problem, or recovers from one it was warned about:

* Routine states (connecting, reconnecting, starting, degraded...) never notify.
* A state must hold for a few polls before it counts, so flapping stays quiet.
* While the internet is down there is one "You're offline" notice, not one per
  service, and services get a grace period after it comes back.

Pure logic: :class:`ServiceAlertTracker` turns status samples into notice specs; the
notification engine shows them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

LABELS: Dict[str, str] = {
    "internet": "Internet",
    "webengine": "Overlay server",
    "twitch": "Twitch",
    "obs": "OBS",
    "psn": "PlayStation",
    "spotify": "Spotify",
    "youtube": "YouTube",
    "discord": "Discord",
    "streamlabs": "Streamlabs",
    "streamelements": "StreamElements",
}

SOURCES: Dict[str, str] = {
    "internet": "app",
    "webengine": "overlay_server",
    "twitch": "twitch",
    "obs": "obs",
    "psn": "psn",
    "spotify": "spotify",
    "youtube": "youtube",
    "discord": "discord",
    "streamlabs": "streamlabs",
    "streamelements": "streamelements",
}

SETTINGS_TABS: Dict[str, str] = {
    "twitch": "Twitch",
    "obs": "OBS",
    "psn": "PSN",
    "spotify": "Spotify",
    "youtube": "YouTube",
    "discord": "Discord",
    "streamlabs": "Streamlabs",
    "streamelements": "StreamElements",
}

HINTS: Dict[str, str] = {
    "twitch": "Open Settings → Twitch and click Reconnect. If it keeps happening, sign in again.",
    "obs": "Check that OBS is open with WebSocket turned on (Tools → WebSocket Server Settings), "
           "and that the port and password in Settings → OBS match.",
    "psn": "Open Settings → PSN and paste a fresh NPSSO code.",
    "spotify": "Open Settings → Spotify and sign in again.",
    "youtube": "Open Settings → YouTube and reconnect your account.",
    "discord": "Check the bot token in Settings → Discord, and that the bot is still in your server.",
    "streamlabs": "Open Settings → Streamlabs and reconnect. An expired token needs a new sign-in.",
    "streamelements": "Open Settings → StreamElements and reconnect. An expired token needs a new sign-in.",
    "webengine": "Overlays in OBS may stop updating. Mycelian restarts the overlay server on its own; "
                 "if this keeps happening, restart Mycelian.",
}
AUTH_HINTS: Dict[str, str] = {
    "twitch": "Open Settings → Twitch and sign in again.",
    "spotify": "Open Settings → Spotify and sign in again.",
    "youtube": "Open Settings → YouTube and sign in again.",
    "discord": "The bot token was rejected. Paste a new one in Settings → Discord.",
    "streamlabs": "Open Settings → Streamlabs and sign in again.",
    "streamelements": "Paste a new JWT token in Settings → StreamElements.",
    "obs": "The WebSocket password was rejected. Copy it from OBS (Tools → WebSocket Server Settings) into Settings → OBS.",
}

_BUSY_WORDS = ("connecting", "reconnecting", "disconnecting", "starting", "restarting", "degraded", "stalled",
               "overloaded", "checking", "initializing", "waiting", "partial", "pending")
_OFF_WORDS = ("not configured", "disabled", "not initialized", "unknown", "not set up", "off")
_PROBLEM_WORDS = ("error", "fail", "disconnected", "expired", "not authenticated", "unauthorized", "offline", "no internet",
                  "unreachable", "crashed", "stopped", "frozen", "invalid", "denied", "lost")
_AUTH_WORDS = ("auth", "token", "expired", "sign", "unauthorized", "password", "invalid", "credentials", "denied")

# Polls (2 s apart) a state must hold before it counts.
STABLE_POLLS = 3
# A service that starts out broken gets longer, since everything is still connecting.
STARTUP_POLLS = 15
# After the internet returns, services get this many polls to reconnect quietly.
RECOVERY_GRACE_POLLS = 15


def classify(key: str, status: Optional[str]) -> str:
    """ok | busy | problem | off."""
    s = (status or "").strip().lower()
    if not s:
        return "off"
    if key == "internet":
        return "ok" if s == "online" else ("problem" if any(w in s for w in ("offline", "no internet", "unreachable")) else "busy")
    if key == "webengine":
        if s == "running":
            return "ok"
        if s in ("crashed", "stopped") or "frozen" in s:
            return "problem"
        return "busy"
    if key == "psn" and s == "token expired":
        return "off"  # psn_service has its own sign-in notice
    if s.startswith("connected") or s in ("online", "running", "ready", "live"):
        return "ok"
    if any(w in s for w in _OFF_WORDS) and not any(w in s for w in ("auth", "token", "expired")):
        return "off"
    if any(w in s for w in _BUSY_WORDS) and not any(w in s for w in ("error", "fail", "expired")):
        return "busy"
    if any(w in s for w in _PROBLEM_WORDS):
        return "problem"
    if "authenticated" in s and "not" in s:
        return "problem"
    return "busy"


@dataclass
class Notice:
    key: str
    kind: str  # problem | recovered | offline | online
    type: str
    title: str
    message: str = ""
    hint: str = ""
    source: str = "app"
    settings_tab: Optional[str] = None
    dedupe_key: str = ""


@dataclass
class _Track:
    stable: str = "unknown"
    candidate: str = ""
    count: int = 0
    raw: str = ""
    warned: bool = False


@dataclass
class ServiceAlertTracker:
    tracks: Dict[str, _Track] = field(default_factory=dict)
    offline: bool = False
    offline_warned: bool = False
    grace: int = 0

    def observe(self, statuses: Dict[str, Optional[str]]) -> List[Notice]:
        out: List[Notice] = []
        if "internet" in statuses:
            out += self._observe_internet(statuses["internet"])
        if self.grace > 0:
            self.grace -= 1
        for key, status in statuses.items():
            if key == "internet":
                continue
            out += self._observe_service(key, status)
        return out

    def forget(self, key: str) -> None:
        self.tracks.pop(key, None)

    def _step(self, key: str, status: Optional[str]) -> Optional[str]:
        """Feed one sample; returns the new stable class when it changes."""
        track = self.tracks.setdefault(key, _Track())
        cls = classify(key, status)
        track.raw = str(status or "")
        if cls == "busy":
            return None  # in-between states never settle anything
        if cls == track.stable:
            track.candidate, track.count = "", 0
            return None
        if cls != track.candidate:
            track.candidate, track.count = cls, 0
        track.count += 1
        needed = STARTUP_POLLS if track.stable == "unknown" and cls == "problem" else STABLE_POLLS
        if track.stable == "unknown" and cls != "problem":
            needed = 1
        if track.count < needed:
            return None
        track.stable, track.candidate, track.count = cls, "", 0
        return cls

    def _observe_internet(self, status: Optional[str]) -> List[Notice]:
        before = self.tracks.get("internet", _Track()).stable
        new = self._step("internet", status)
        if new is None:
            return []
        if new == "problem":
            self.offline = True
            self.offline_warned = True
            for key, track in self.tracks.items():
                if key != "internet":
                    track.candidate, track.count = "", 0
            return [Notice("internet", "offline", "warning", "You're offline",
                           "Twitch, chat, alerts from online services and other connections pause until the internet is back.",
                           "Check your network connection. Mycelian reconnects everything on its own once you're back online.",
                           source="app", dedupe_key="svc:internet:offline")]
        if new == "ok" and before == "problem":
            self.offline = False
            self.grace = RECOVERY_GRACE_POLLS
            if self.offline_warned:
                self.offline_warned = False
                return [Notice("internet", "online", "success", "Back online", "Reconnecting your services.",
                               source="app", dedupe_key="svc:internet:online")]
        return []

    def _observe_service(self, key: str, status: Optional[str]) -> List[Notice]:
        if self.offline:
            # Everything is down because the internet is; the offline notice covers it.
            track = self.tracks.setdefault(key, _Track())
            track.candidate, track.count = "", 0
            track.raw = str(status or "")
            return []
        track = self.tracks.setdefault(key, _Track())
        before = track.stable
        new = self._step(key, status)
        if new is None:
            return []
        label = LABELS.get(key, key)
        if new == "problem":
            if self.grace > 0:
                # Still reconnecting after the internet came back: wait out the grace period.
                track.stable = before
                track.candidate, track.count = "problem", 0
                return []
            raw = track.raw.lower()
            auth = any(w in raw for w in _AUTH_WORDS) and key in AUTH_HINTS
            track.warned = True
            if key == "webengine":
                title = f"{label} {track.raw.lower() or 'stopped'}"
            elif auth:
                title = f"{label} needs you to sign in again"
            else:
                title = f"{label} disconnected"
            return [Notice(key, "problem", "error" if auth or key == "webengine" else "warning", title,
                           f"Status: {track.raw}" if track.raw else "",
                           AUTH_HINTS.get(key, "") if auth else HINTS.get(key, ""),
                           source=SOURCES.get(key, "app"), settings_tab=SETTINGS_TABS.get(key),
                           dedupe_key=f"svc:{key}:problem")]
        if new == "ok" and before == "problem" and track.warned:
            track.warned = False
            return [Notice(key, "recovered", "success", f"{label} reconnected", source=SOURCES.get(key, "app"),
                           settings_tab=SETTINGS_TABS.get(key), dedupe_key=f"svc:{key}:recovered")]
        return []
