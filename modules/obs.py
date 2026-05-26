"""OBS Studio integration (WebSocket).

All socket I/O runs on :mod:`modules.obs_service`'s ``ObsWebSocket`` daemon thread;
callers enqueue work via :func:`obs_service.enqueue_obs_request` or
:func:`obs_service.enqueue_refresh_snapshot`. This module preserves the legacy import
path ``modules.obs``.
"""

from __future__ import annotations

from .obs_service import ObsServiceImpl, obs_service, start_obs_service

__all__ = ["ObsServiceImpl", "obs_service", "start_obs_service"]
