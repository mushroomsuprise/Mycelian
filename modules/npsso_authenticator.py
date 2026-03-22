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
import json
import logging
import threading
import time
import webbrowser
from typing import Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PSN_HOMEPAGE_URL = "https://www.playstation.com"
NPSSO_URL = "https://ca.account.sony.com/api/v1/ssocookie"


@dataclass
class NpssoResult:
    """Result of NPSSO acquisition attempt"""
    success: bool
    npsso_code: str = ""
    error_message: str = ""


class NpssoAuthenticator:
    """Handles NPSSO token acquisition via browser-based authentication flow"""

    def __init__(self):
        self._npsso_code: Optional[str] = None
        self._completed = threading.Event()

    def start_auth_flow(self, on_complete: Callable[[NpssoResult], None]) -> None:
        """Start the NPSSO authentication flow"""
        try:
            # Show instruction dialog
            self._show_instruction_dialog(on_complete)
        except Exception as e:
            logger.error(f"Error starting NPSSO auth flow: {e}")
            on_complete(NpssoResult(success=False, error_message=str(e)))

    def _show_instruction_dialog(self, on_complete: Callable[[NpssoResult], None]) -> None:
        """Show the instruction dialog for NPSSO acquisition"""
        from nicegui import ui

        def on_continue():
            """Handle continue button click"""
            dialog.close()
            self._fetch_npsso_token(on_complete)

        def enable_continue():
            """Enable the continue button after countdown"""
            continue_btn.enable()
            continue_btn.text = "Continue"

        with ui.dialog() as dialog:
            with ui.card().classes("w-[600px] p-6"):
                ui.label("Connect PlayStation Network").classes("text-2xl font-bold mb-4")

                with ui.column().classes("gap-4"):
                    ui.label(
                        "To connect your PlayStation Network account, follow these steps:"
                    ).classes("text-lg mb-2")

                    # Step 1
                    with ui.row().classes("gap-3 items-start"):
                        ui.badge("1", color="primary").classes("rounded-full mt-1")
                        with ui.column().classes("gap-1 flex-grow"):
                            ui.label("Open your web browser and go to PlayStation.com").classes("font-medium")
                            ui.label("Sign in to your PlayStation Network account.").classes("text-sm secondary-text")

                    # Step 2
                    with ui.row().classes("gap-3 items-start"):
                        ui.badge("2", color="primary").classes("rounded-full mt-1")
                        with ui.column().classes("gap-1 flex-grow"):
                            ui.label("Wait for the page to fully load after signing in").classes("font-medium")
                            ui.label("Do not close the browser window.").classes("text-sm secondary-text")

                    # Step 3
                    with ui.row().classes("gap-3 items-start"):
                        ui.badge("3", color="primary").classes("rounded-full mt-1")
                        ui.label("Click 'Continue' below to complete the connection").classes("font-medium")

                    ui.separator().classes("my-4")

                    ui.label(
                        "Note: Your browser will open automatically to retrieve your authentication token."
                    ).classes("text-sm text-orange-600 font-medium")

                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")

                        # Continue button - disabled for 10 seconds
                        continue_btn = ui.button(
                            "Continue (10)",
                            on_click=on_continue,
                            color="primary"
                        )
                        continue_btn.disable()

                        # Countdown timer
                        def countdown_timer(remaining: int):
                            if remaining > 0:
                                continue_btn.text = f"Continue ({remaining})"
                                ui.timer(1.0, lambda: countdown_timer(remaining - 1), once=True)
                            else:
                                enable_continue()

                        ui.timer(0.1, lambda: countdown_timer(10), once=True)

        dialog.open()

    def _fetch_npsso_token(self, on_complete: Callable[[NpssoResult], None]) -> None:
        """Fetch the NPSSO token from the Sony endpoint"""
        def fetch_token():
            try:
                # Open browser to the NPSSO endpoint
                logger.info("Opening browser to NPSSO endpoint")
                webbrowser.open(NPSSO_URL)

                # Wait a moment for the browser to open
                time.sleep(2)

                # Fetch the NPSSO token directly
                import urllib.request
                import urllib.error

                logger.info("Fetching NPSSO token from Sony endpoint")
                req = urllib.request.Request(
                    NPSSO_URL,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Accept': 'application/json',
                    }
                )

                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode('utf-8'))
                        npsso = data.get("npsso")

                        if npsso and len(npsso) == 64:  # NPSSO tokens are 64 characters
                            logger.info("NPSSO token acquired successfully")
                            on_complete(NpssoResult(success=True, npsso_code=npsso))
                        else:
                            logger.error(f"Invalid NPSSO token received: {npsso}")
                            on_complete(NpssoResult(
                                success=False,
                                error_message="Invalid token format received. Please ensure you're signed into PlayStation Network."
                            ))
                    else:
                        logger.error(f"HTTP error fetching NPSSO: {response.status}")
                        on_complete(NpssoResult(
                            success=False,
                            error_message=f"Failed to fetch token (HTTP {response.status}). Please try again."
                        ))

            except urllib.error.HTTPError as e:
                logger.error(f"HTTP error: {e}")
                if e.code == 401:
                    on_complete(NpssoResult(
                        success=False,
                        error_message="Not signed in. Please go to PlayStation.com and sign in first."
                    ))
                else:
                    on_complete(NpssoResult(
                        success=False,
                        error_message=f"Network error: {e.code}. Please check your internet connection."
                    ))
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                on_complete(NpssoResult(
                    success=False,
                    error_message="Invalid response format. Please try again."
                ))
            except Exception as e:
                logger.error(f"Error fetching NPSSO token: {e}")
                on_complete(NpssoResult(
                    success=False,
                    error_message=f"Unexpected error: {str(e)}. Please try again."
                ))

        # Run in background thread to avoid blocking UI
        thread = threading.Thread(target=fetch_token, daemon=True)
        thread.start()


def start_npsso_auth_flow(on_complete: Callable[[NpssoResult], None]) -> None:
    """Convenience function to start NPSSO authentication flow"""
    authenticator = NpssoAuthenticator()
    authenticator.start_auth_flow(on_complete)