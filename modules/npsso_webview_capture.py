#!/usr/bin/env python3
"""
Subprocess-only NPSSO capture using pywebview.

Sony's ssocookie endpoint returns JSON only when the request carries an authenticated
session. This module loads PlayStation.com in an isolated WebView so sign-in and
token retrieval share the same cookie jar.

Run from project root:
    python -m modules.npsso_webview_capture

On success: prints the NPSSO token to stdout (single line, typically 64 chars) and exits 0.
On failure: prints a message to stderr and exits non-zero.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

import requests

PSN_START_URL = "https://www.playstation.com"
NPSSO_URL = "https://ca.account.sony.com/api/v1/ssocookie"

_REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Synchronous read — avoids Promise handling quirks on JSON viewer pages.
_EXTRACT_JS = """
(function() {
  try {
    var chunks = [];
    if (document.body) {
      chunks.push(document.body.innerText || '');
      chunks.push(document.body.textContent || '');
    }
    if (document.documentElement) {
      chunks.push(document.documentElement.innerText || '');
      chunks.push(document.documentElement.textContent || '');
    }
    var pres = document.querySelectorAll('pre');
    for (var i = 0; i < pres.length; i++) {
      chunks.push(pres[i].innerText || pres[i].textContent || '');
    }
    return chunks.join('\\n').trim();
  } catch (e) {
    return '';
  }
})()
"""

# Sony returns a 64-char value (base64 / base64url-like), not hex-only.
_NPSSO_JSON_RE = re.compile(r'"npsso"\s*:\s*"([^"]+)"')
_NPSSO_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{64}$")


def _normalize_npsso_value(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not _NPSSO_TOKEN_RE.fullmatch(s):
        return None
    return s


def _parse_npsso_from_text(text: str) -> str | None:
    if not text:
        return None
    text = text.strip()
    m = _NPSSO_JSON_RE.search(text)
    if m:
        return _normalize_npsso_value(m.group(1))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    npsso = data.get("npsso")
    return _normalize_npsso_value(npsso) if isinstance(npsso, str) else None


def _parse_npsso_from_eval_result(result: object) -> str | None:
    """evaluate_js may return str, dict (parsed JSON), or other types."""
    if result is None:
        return None
    if isinstance(result, dict):
        npsso = result.get("npsso")
        t = _normalize_npsso_value(npsso) if isinstance(npsso, str) else None
        if t:
            return t
        try:
            return _parse_npsso_from_text(json.dumps(result))
        except (TypeError, ValueError):
            return None
    if isinstance(result, str):
        return _parse_npsso_from_text(result)
    return _parse_npsso_from_text(str(result))


def _cookies_from_webview(window) -> dict[str, str]:
    """Merge pywebview get_cookies() results into a single name->value dict."""
    jar: dict[str, str] = {}
    try:
        raw = window.get_cookies()
    except Exception:
        return jar
    if not raw:
        return jar
    for sc in raw:
        try:
            for name, morsel in sc.items():
                jar[name] = morsel.value
        except Exception:
            continue
    return jar


def _try_http_npsso(window) -> str | None:
    """
    Same NPSSO URL as the browser, but with this WebView's session cookies.
    Avoids racing the JSON viewer DOM (loaded can fire before body/url match).
    """
    cookies = _cookies_from_webview(window)
    if not cookies:
        return None
    try:
        r = requests.get(
            NPSSO_URL,
            cookies=cookies,
            headers=_REQUEST_HEADERS,
            timeout=45,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return _parse_npsso_from_text(r.text)


def _try_dom_npsso(window) -> str | None:
    """Read token from whatever the WebKit JSON viewer put in the DOM."""
    try:
        raw = window.evaluate_js(_EXTRACT_JS)
    except Exception:
        return None
    return _parse_npsso_from_eval_result(raw)


def main() -> int:
    import webview
    from webview.menu import Menu, MenuAction

    window: webview.Window
    state: dict = {
        "fetching": False,
        "token": None,
        "error": None,
        "resolving": False,
    }

    def finish_success(token: str) -> None:
        if state["token"] is not None:
            return
        state["token"] = token
        print(
            f"[NPSSO_SUBPROC] pid={os.getpid()} finish_success token_len={len(token)}",
            file=sys.stderr,
            flush=True,
        )
        try:
            window.destroy()
        except Exception:
            pass

    def finish_error(msg: str) -> None:
        if state["token"] is not None:
            return
        state["error"] = msg
        state["fetching"] = False
        try:
            window.destroy()
        except Exception:
            pass

    def _navigation_ready(url_lower: str) -> bool:
        return (
            "ssocookie" in url_lower
            or "ca.account.sony.com" in url_lower
            or "my.account.sony.com" in url_lower
        )

    def resolve_npsso_worker() -> None:
        """
        Wait until ssocookie / account host is actually loaded, then read NPSSO
        via HTTP+cookies (primary) or DOM (fallback).
        """
        try:
            for attempt in range(80):
                if state["token"] is not None:
                    return
                try:
                    cur = (window.get_current_url() or "").lower()
                except Exception:
                    cur = ""
                ready = _navigation_ready(cur) or attempt >= 18

                if ready:
                    # Prefer DOM when the user can already see JSON — HTTP+cookie
                    # mirroring sometimes misses cookies or gets a different body.
                    if _navigation_ready(cur):
                        for _ in range(6):
                            if state["token"] is not None:
                                return
                            token = _try_dom_npsso(window)
                            if token:
                                finish_success(token)
                                return
                            time.sleep(0.12)
                    token = _try_http_npsso(window)
                    if token:
                        finish_success(token)
                        return

                time.sleep(0.2)

            finish_error(
                "NPSSO could not be retrieved. Sign in on PlayStation.com, then use "
                "the menu again, or paste the token manually (Help)."
            )
        finally:
            state["resolving"] = False

    def ensure_resolve_started() -> None:
        if not state["fetching"] or state["token"] is not None:
            return
        if state["resolving"]:
            return
        state["resolving"] = True
        threading.Thread(target=resolve_npsso_worker, daemon=True).start()

    def on_loaded() -> None:
        ensure_resolve_started()

    def start_fetch() -> None:
        if state["token"] is not None:
            return
        print(
            f"[NPSSO_SUBPROC] pid={os.getpid()} menu: load NPSSO URL",
            file=sys.stderr,
            flush=True,
        )
        state["fetching"] = True
        try:
            window.load_url(NPSSO_URL)
        except Exception as e:
            finish_error(f"Could not load NPSSO URL: {e}")
            return
        # JSON / API pages may not fire `loaded` like HTML; also kick resolve after navigation.
        threading.Timer(0.45, lambda: ensure_resolve_started()).start()

    window = webview.create_window(
        "Mycelian — PlayStation sign-in",
        PSN_START_URL,
        width=960,
        height=820,
        text_select=True,
    )
    window.events.loaded += on_loaded

    menu = [
        Menu(
            "NPSSO",
            [
                MenuAction(
                    "I am signed in — retrieve NPSSO token",
                    start_fetch,
                ),
            ],
        )
    ]

    print(
        f"[NPSSO_SUBPROC] pid={os.getpid()} webview.start() (blocks until window exits)",
        file=sys.stderr,
        flush=True,
    )
    webview.start(
        menu=menu,
        debug=False,
        private_mode=False,
    )
    print(
        f"[NPSSO_SUBPROC] pid={os.getpid()} webview.start() returned",
        file=sys.stderr,
        flush=True,
    )

    if state["token"]:
        print(state["token"], flush=True)
        return 0

    if state["error"]:
        print(state["error"], file=sys.stderr, flush=True)
        return 1

    print(
        "Window closed before NPSSO was retrieved. Use the menu: "
        "NPSSO → I am signed in — retrieve NPSSO token.",
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
