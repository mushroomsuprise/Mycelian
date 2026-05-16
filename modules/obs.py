"""OBS Studio integration (WebSocket).

Runtime behavior lives in :mod:`modules.obs_service` (daemon thread).
This module preserves the legacy import path ``modules.obs``.
"""

from __future__ import annotations

from .obs_service import ObsServiceImpl, obs_service, start_obs_service

__all__ = ["ObsServiceImpl", "obs_service", "start_obs_service"]
