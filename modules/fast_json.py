# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Faster JSON for the overlay server (WE-51), with the standard library as fallback.

orjson (already shipped: NiceGUI depends on it) encodes several times faster than
``json``. It is only used where the output means the same to the reader:

* Socket.IO packets (``SocketIO(json=fast_json)``): same keys in the same order;
  non-ASCII text is sent as UTF-8 instead of ``\\uXXXX`` escapes, which a browser
  parses to the same string.
* Flask responses (:func:`install_flask_provider`): keys stay sorted as before, and
  dates, dataclasses and other types Flask converts go through Flask's own
  ``default`` exactly as before.

Anything orjson can't encode the same way (very large integers, ``NaN`` from
``loads``, extra ``json`` keyword arguments) falls back to ``json``.
"""

from __future__ import annotations

import json as _json
from typing import Any

try:  # pragma: no cover - exercised implicitly when installed
    import orjson
except Exception:  # pragma: no cover - orjson missing on unusual platforms
    orjson = None  # type: ignore[assignment]

_SOCKET_OPTS = orjson.OPT_NON_STR_KEYS if orjson is not None else 0


def dumps(obj: Any, *args: Any, **kwargs: Any) -> str:
    """``json.dumps`` for Socket.IO packets (python-socketio passes ``separators``).

    ``default`` works as in ``json`` for types neither encoder knows. Dates and
    dataclasses, which orjson would otherwise encode its own way, are handed to
    ``default`` as ``json`` would.
    """
    if orjson is not None and not args and set(kwargs) <= {"separators", "default"}:
        option = _SOCKET_OPTS
        if "default" in kwargs:
            option |= orjson.OPT_PASSTHROUGH_DATETIME | orjson.OPT_PASSTHROUGH_DATACLASS
        try:
            return orjson.dumps(obj, default=kwargs.get("default"), option=option).decode("utf-8")
        except (TypeError, ValueError):
            pass
    return _json.dumps(obj, *args, **kwargs)


def loads(s: Any, *args: Any, **kwargs: Any) -> Any:
    if orjson is not None and not args and not kwargs:
        try:
            return orjson.loads(s)
        except (TypeError, ValueError):
            pass  # e.g. NaN / Infinity tokens, which json accepts
    return _json.loads(s, *args, **kwargs)


def install_flask_provider(app: Any) -> bool:
    """Use orjson for ``app``'s JSON responses. Returns False when it can't."""
    if orjson is None:
        return False
    try:
        from flask.json.provider import DefaultJSONProvider
    except Exception:
        return False

    class OrjsonProvider(DefaultJSONProvider):
        def dumps(self, obj: Any, **kwargs: Any) -> str:
            # Flask's response() passes separators (compact) or indent (debug).
            if set(kwargs) <= {"separators"}:
                option = (
                    orjson.OPT_NON_STR_KEYS
                    | orjson.OPT_PASSTHROUGH_DATETIME
                    | orjson.OPT_PASSTHROUGH_DATACLASS
                )
                if self.sort_keys:
                    option |= orjson.OPT_SORT_KEYS
                try:
                    return orjson.dumps(obj, default=self.default, option=option).decode("utf-8")
                except (TypeError, ValueError):
                    pass
            return super().dumps(obj, **kwargs)

        def loads(self, s: Any, **kwargs: Any) -> Any:
            if not kwargs:
                try:
                    return orjson.loads(s)
                except (TypeError, ValueError):
                    pass
            return super().loads(s, **kwargs)

    app.json = OrjsonProvider(app)
    return True
