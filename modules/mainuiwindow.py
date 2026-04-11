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
import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from multiprocessing import current_process
from typing import Any, Dict, List, Optional

from nicegui import app, events, ui

from modules import updater


# Version information
def get_version():
    """Get application version from version.txt"""
    try:
        with open("version.txt", "r") as f:
            content = f.read()
            # Parse version from the file
            for line in content.split("\n"):
                if "filevers=" in line or "prodvers=" in line:
                    # Extract version tuple
                    import re

                    match = re.search(r"\((\d+),(\d+),(\d+),(\d+)\)", line)
                    if match:
                        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    except Exception:
        pass
    return "1.3.3"  # Fallback


# Global flag to track if UI elements have been created
_ui_elements_created = False

# Global splash screen references
_splash_dialog = None
_splash_progress = None
_splash_status = None
from modules.uiwindows.activity_feed import (
    add_alert_to_feed,
    create_activity_feed_tab,
    stop_alert_processor,
)
from modules.uiwindows.customsources import create_custom_sources_tab

from . import alertutils, database_manager, dataobjects, web_engine
from .theme_manager import get_theme_manager, generate_css_variables
from .ui_styles import get_full_theme_css

logger = logging.getLogger(__name__)

# Properly initialize firebase and state managers
# Legacy function removed - replaced by init_app_state_background for non-blocking initialization

# Setup app window properties
app.native.window_args["title"] = "Mycelian"
app.native.window_args["min_size"] = (1400, 850)
app.native.window_args["maximized"] = True
ui.colors(
    primary="var(--color-primary)",
)


# Theme CSS will be injected dynamically when theme is applied
_base_css_injected = False


def _get_quasar_brand_css(theme) -> str:
    """Generate CSS that overrides Quasar's brand color variables to match the theme.

    Quasar buttons using .props("color=primary") or .props("color=secondary")
    use --q-primary and --q-secondary CSS variables. This generates CSS to set
    those variables so all Quasar-styled buttons follow the theme colors.

    Args:
        theme: ThemeColors instance with the current theme colors

    Returns:
        CSS string with Quasar brand variable overrides
    """
    primary = theme.primary or "rgb(115, 0, 255)"
    info = theme.info or "rgb(59, 130, 246)"
    success = theme.success or "rgb(34, 197, 94)"
    warning = theme.warning or "rgb(234, 179, 8)"
    error = theme.error or "rgb(239, 68, 68)"

    return f"""
:root {{
    --q-primary: {primary};
    --q-secondary: {info};
    --q-positive: {success};
    --q-negative: {error};
    --q-warning: {warning};
    --q-info: {info};
}}
"""


def _sync_quasar_brand_colors_js(theme) -> None:
    """Sync Quasar brand colors via JavaScript (for dynamic theme switches).

    Only call this after the event loop is running (not during initial setup).

    Args:
        theme: ThemeColors instance with the current theme colors
    """
    primary = theme.primary or "rgb(115, 0, 255)"
    info = theme.info or "rgb(59, 130, 246)"
    success = theme.success or "rgb(34, 197, 94)"
    warning = theme.warning or "rgb(234, 179, 8)"
    error = theme.error or "rgb(239, 68, 68)"

    js_code = f"""
    (function() {{
        var root = document.documentElement;
        root.style.setProperty('--q-primary', '{primary}');
        root.style.setProperty('--q-secondary', '{info}');
        root.style.setProperty('--q-positive', '{success}');
        root.style.setProperty('--q-negative', '{error}');
        root.style.setProperty('--q-warning', '{warning}');
        root.style.setProperty('--q-info', '{info}');

        // Update existing style tag if present
        var style = document.getElementById('mycelian-quasar-brand');
        if (style) {{
            style.textContent = ':root {{ --q-primary: {primary}; --q-secondary: {info}; --q-positive: {success}; --q-negative: {error}; --q-warning: {warning}; --q-info: {info}; }}';
        }}
    }})();
    """
    ui.run_javascript(js_code)


def apply_theme(theme_name: str):
    """Apply the selected theme to the application

    Args:
        theme_name: Name of the theme to apply
    """
    global _base_css_injected
    theme_manager = get_theme_manager()

    # Load themes if not already loaded
    if not theme_manager._loaded_themes:
        theme_manager.load_themes_from_directory()

    # Set current theme in manager
    theme_manager.set_theme(theme_name)

    # Get theme colors
    theme = theme_manager.get_theme()
    if not theme:
        logger.error(f"Failed to load theme: {theme_name}")
        return

    # Generate theme CSS variables
    theme_css = generate_css_variables(theme)

    # Set NiceGUI dark mode based on theme_type (for body--dark class)
    # This ensures some NiceGUI components work correctly
    theme_type = theme_manager.get_theme_type(theme_name)
    if theme_type == "dark":
        ui.dark_mode().enable()
    else:
        ui.dark_mode().disable()

    # Inject base CSS once
    if not _base_css_injected:
        # First inject the CSS variables
        ui.add_head_html(
            f"<style id='mycelian-theme-vars'>{theme_css}</style>", shared=True
        )
        # Then add base CSS that references variables
        base_css = get_full_theme_css() + ACTIVITY_FEED_CSS
        ui.add_head_html(
            f"<style id='mycelian-base-css'>{base_css}</style>", shared=True
        )
        # Inject Quasar brand color overrides as a style tag (safe before event loop)
        quasar_css = _get_quasar_brand_css(theme)
        ui.add_head_html(
            f"<style id='mycelian-quasar-brand'>{quasar_css}</style>", shared=True
        )

        _base_css_injected = True

        logger.info(
            f"Initial theme CSS injected for theme: {theme_name} ({theme_type})"
        )
        return

    # Update the CSS variables via JavaScript for theme switches
    # This allows hot-swapping without page reload
    # Escape backticks and special characters for JavaScript string
    escaped_theme_css = theme_css.replace("`", "\\`").replace("${", "\\${")

    js_code = f"""
    (function() {{
        var style = document.getElementById('mycelian-theme-vars');
        if (!style) {{
            style = document.createElement('style');
            style.id = 'mycelian-theme-vars';
            document.head.insertBefore(style, document.head.firstChild);
        }}
        style.textContent = `{escaped_theme_css}`;
    }})();
    """
    ui.run_javascript(js_code)

    # Sync Quasar brand colors via JS (event loop is running for dynamic switches)
    _sync_quasar_brand_colors_js(theme)

    # Broadcast theme update to external HTML templates (OBS docks, etc.)
    if (
        hasattr(web_engine, "web_engine_instance")
        and web_engine.web_engine_instance is not None
    ):
        web_engine.web_engine_instance.broadcast_theme_update(
            theme_css=theme_css,
            theme_name=theme_name,
            theme_type=theme_type,
        )

    logger.info(f"Applied theme: {theme_name} ({theme_type})")


# Activity feed specific CSS that includes animations
ACTIVITY_FEED_CSS = """
/* Activity Feed Animations and Styles */
.alert-card {
    margin-bottom: 0.25rem !important;
    padding: 0.25rem 0.75rem !important;
    border-radius: 0.25rem !important;
    background-color: var(--color-bg-surface) !important;
    transition: all 0.2s ease-in-out !important;
    height: 28px !important;
    display: flex !important;
    align-items: center !important;
    animation: slideIn 0.3s ease-out !important;
    position: relative !important;
    overflow: hidden !important;
}

@keyframes slideIn {
    0% {
        transform: translateX(-20px);
        opacity: 0;
    }
    100% {
        transform: translateX(0);
        opacity: 1;
    }
}

.alert-card.recent {
    border-left: 2px solid var(--color-primary) !important;
    animation: slideIn 0.3s ease-out, pulseBorder 2s infinite !important;
    position: relative !important;
}

.alert-card.recent.not-visible {
    animation-play-state: paused, paused !important;
}

@keyframes pulseBorder {
    0% {
        box-shadow: 0 0 0 0 rgba(115, 0, 255, 0.4);
    }
    70% {
        box-shadow: 0 0 0 4px rgba(115, 0, 255, 0);
    }
    100% {
        box-shadow: 0 0 0 0 rgba(115, 0, 255, 0);
    }
}

.new-badge {
    position: absolute;
    top: 2px;
    right: 2px;
    background: linear-gradient(to right, rgba(147, 51, 234, 0.25), rgba(147, 51, 234, 0.15));
    color: rgb(147, 51, 234);
    font-size: 10px;
    padding: 0px 3px;
    border-radius: 4px;
    min-width: 24px;
    height: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: inset 0 0 2px rgba(147, 51, 234, 0.5);
    border: 1px solid rgba(147, 51, 234, 0.2);
    animation: pulse 2s infinite;
}

.new-badge.hidden {
    display: none !important;
}

.new-badge.not-visible {
    animation-play-state: paused !important;
}

.alert-card:hover {
    background-color: var(--color-primary-light) !important;
}

.alert-card > div {
    display: flex !important;
    align-items: center !important;
    height: 100% !important;
}

.alert-card .text-sm {
    line-height: 1 !important;
}

.replay-button {
    min-width: 20px !important;
    width: 20px !important;
    height: 18px !important;
    padding: 0 !important;
    margin-right: 8px !important;
    background: var(--color-primary-light) !important;
    border-radius: 3px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}

.replay-button:hover {
    background: rgba(115, 0, 255, 0.2) !important;
}

.replay-button i {
    font-size: 12px !important;
    width: 12px !important;
    height: 12px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}

/* Alert type badges */
.badge {
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    position: relative;
    overflow: hidden;
    transition: all 0.3s ease;
    box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 1px 1px rgba(255, 255, 255, 0.1);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 60px;
    text-align: center;
    border: 1px solid rgba(255, 255, 255, 0.15);
    height: 18px;
    line-height: 1;
    margin-right: 6px;
    background-image: linear-gradient(to bottom, rgba(255, 255, 255, 0.1), rgba(0, 0, 0, 0.2));
    animation: badgePulse 3s infinite;
}

.badge.not-visible {
    animation-play-state: paused !important;
}

.badge::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
    animation: none;
}

@keyframes badgePulse {
    0% { box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 3px rgba(255, 255, 255, 0.1); }
    50% { box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 6px rgba(255, 255, 255, 0.2); }
    100% { box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 3px rgba(255, 255, 255, 0.1); }
}

/* Tier animations */
.badge.sub.tier2, .badge.resub.tier2 {
    animation: badgePulse 3s infinite, tier2Glow 2s infinite;
}

.badge.sub.tier2.not-visible, .badge.resub.tier2.not-visible {
    animation-play-state: paused, paused !important;
}

@keyframes tier2Glow {
    0% { border-color: rgba(255, 255, 255, 0.3); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2); }
    50% { border-color: rgba(0, 188, 212, 0.5); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 8px rgba(0, 188, 212, 0.4); }
    100% { border-color: rgba(255, 255, 255, 0.3); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2); }
}

.badge.sub.tier3, .badge.resub.tier3 {
    animation: badgePulse 3s infinite, tier3Glow 2s infinite;
}

.badge.sub.tier3::before, .badge.resub.tier3::before {
    animation: tier3Shimmer 3s infinite;
}

.badge.sub.tier3.not-visible, .badge.resub.tier3.not-visible {
    animation-play-state: paused, paused !important;
}

.badge.sub.tier3.not-visible::before, .badge.resub.tier3.not-visible::before {
    animation-play-state: paused !important;
}

@keyframes tier3Glow {
    0% { border-color: rgba(255, 255, 255, 0.3); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2); }
    50% { border-color: rgba(255, 215, 0, 0.6); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 12px rgba(255, 215, 0, 0.6); }
    100% { border-color: rgba(255, 255, 255, 0.3); box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2); }
}

@keyframes tier3Shimmer {
    0% { left: -100%; opacity: 0.5; }
    50% { opacity: 0.8; }
    100% { left: 100%; opacity: 0.5; }
}

/* Badge colors */
.badge.follow {
    background: linear-gradient(to bottom, rgba(145, 71, 255, 0.2), rgba(122, 61, 212, 0.4));
    color: white;
    border: 1px solid rgba(145, 71, 255, 0.4);
}

.badge.bits {
    background: linear-gradient(to bottom, rgba(255, 107, 107, 0.2), rgba(230, 76, 76, 0.4));
    color: white;
    border: 1px solid rgba(255, 107, 107, 0.4);
}

.badge.points {
    background: linear-gradient(to bottom, rgba(76, 175, 80, 0.2), rgba(56, 142, 60, 0.4));
    color: white;
    border: 1px solid rgba(76, 175, 80, 0.4);
}

.badge.sub {
    background: linear-gradient(to bottom, rgba(0, 188, 212, 0.15), rgba(33, 150, 243, 0.25));
    border: 1px solid rgba(0, 188, 212, 0.4);
}

.badge.resub {
    background: linear-gradient(to bottom, rgba(156, 39, 176, 0.15), rgba(233, 30, 99, 0.25));
    border: 1px solid rgba(156, 39, 176, 0.4);
}

.badge.sub.tier2 {
    background: linear-gradient(to bottom, rgba(0, 188, 212, 0.15), rgba(33, 150, 243, 0.25));
    border: 1px solid rgba(0, 188, 212, 0.4);
}

.badge.sub.tier2::after {
    content: 'T2';
    position: absolute;
    top: 0;
    right: 0;
    background: rgba(0, 0, 0, 0.6);
    color: white;
    font-size: 0.55rem;
    padding: 0px 3px;
    border-radius: 0 3px 0 3px;
    font-weight: 700;
    letter-spacing: 0.05em;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.3);
}

.badge.sub.tier3 {
    background: linear-gradient(to bottom, rgba(0, 188, 212, 0.15), rgba(63, 81, 181, 0.25));
    border: 1px solid rgba(0, 188, 212, 0.4);
}

.badge.sub.tier3::after {
    content: 'T3';
    position: absolute;
    top: 0;
    right: 0;
    background: rgba(0, 0, 0, 0.6);
    color: white;
    font-size: 0.55rem;
    padding: 0px 3px;
    border-radius: 0 3px 0 3px;
    font-weight: 700;
    letter-spacing: 0.05em;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.3);
}

.badge.resub.tier2 {
    background: linear-gradient(to bottom, rgba(156, 39, 176, 0.15), rgba(233, 30, 99, 0.25));
    border: 1px solid rgba(156, 39, 176, 0.4);
}

.badge.resub.tier2::after {
    content: 'T2';
    position: absolute;
    top: 0;
    right: 0;
    background: rgba(0, 0, 0, 0.6);
    color: white;
    font-size: 0.55rem;
    padding: 0px 3px;
    border-radius: 0 3px 0 3px;
    font-weight: 700;
    letter-spacing: 0.05em;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.3);
}

.badge.resub.tier3 {
    background: linear-gradient(to bottom, rgba(156, 39, 176, 0.15), rgba(103, 58, 183, 0.25));
    border: 1px solid rgba(156, 39, 176, 0.4);
}

.badge.resub.tier3::after {
    content: 'T3';
    position: absolute;
    top: 0;
    right: 0;
    background: rgba(0, 0, 0, 0.6);
    color: white;
    font-size: 0.55rem;
    padding: 0px 3px;
    border-radius: 0 3px 0 3px;
    font-weight: 700;
    letter-spacing: 0.05em;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.3);
}

.badge.giftsub {
    background-color: rgba(255, 215, 0, 0.2) !important;
    color: #ffd700 !important;
    border: 1px solid rgba(255, 215, 0, 0.3) !important;
}

.badge.donation {
    background-color: rgba(0, 255, 128, 0.2) !important;
    color: #80ffbf !important;
    border: 1px solid rgba(128, 255, 191, 0.3) !important;
}

.badge.raid {
    background-color: rgba(255, 0, 0, 0.2) !important;
    color: #ff8080 !important;
    border: 1px solid rgba(255, 128, 128, 0.3) !important;
}

.badge.hype_train {
    background-color: rgba(255, 128, 0, 0.2) !important;
    color: #ffbf80 !important;
    border: 1px solid rgba(255, 191, 128, 0.3) !important;
}

.control-button.paused {
    background-color: var(--color-primary-light) !important;
    color: #b980ff !important;
}

.alert-actions {
    opacity: 0;
    transition: opacity 0.2s ease-in-out !important;
}

.alert-card:hover .alert-actions {
    opacity: 1;
}

/* Persistent update notification styling */
.persistent-update-notification {
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 4px !important;
    z-index: 9999 !important;
}

.persistent-update-notification .update-btn {
    background-color: var(--color-primary) !important;
    color: white !important;
    text-transform: none !important;
    border-radius: 3px !important;
}

.persistent-update-notification .update-btn:hover {
    background-color: var(--color-primary-hover) !important;
}

.persistent-update-notification .cancel-btn {
    background-color: #6c757d !important;
    color: white !important;
    text-transform: none !important;
    border-radius: 3px !important;
}

/* Download progress notification styling */
.download-progress-notification {
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 4px !important;
    z-index: 9998 !important;
}
"""

# Add animation observer script
ui.add_head_html(
    """
<script>
// Intersection Observer for animation performance optimization
(function() {
    let animationObserver = null;
    
    function initializeAnimationObserver() {
        // Clean up existing observer
        if (animationObserver) {
            animationObserver.disconnect();
        }
        
        // Create new observer with optimized settings
        animationObserver = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                const element = entry.target;
                
                if (entry.isIntersecting) {
                    // Element is visible - resume animations
                    element.classList.remove('not-visible');
                } else {
                    // Element is not visible - pause animations
                    element.classList.add('not-visible');
                }
            });
        }, {
            // Only trigger when element is completely out of view or comes into view
            threshold: [0, 0.1],
            // Add some margin to avoid too frequent triggers
            rootMargin: '50px'
        });
        
        // Observe all animated elements
        observeAnimatedElements();
    }
    
    function observeAnimatedElements() {
        if (!animationObserver) return;
        
        // Find all elements with animations
        const animatedElements = document.querySelectorAll('.alert-card.recent, .new-badge, .badge');
        
        animatedElements.forEach(function(element) {
            animationObserver.observe(element);
        });
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeAnimationObserver);
    } else {
        initializeAnimationObserver();
    }
    
    // Re-observe when new elements are added (for dynamic content)
    const mutationObserver = new MutationObserver(function(mutations) {
        let shouldReobserve = false;
        
        mutations.forEach(function(mutation) {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach(function(node) {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        // Check if the added node or its children have animations
                        if (node.matches && (node.matches('.alert-card.recent, .new-badge, .badge') || 
                            node.querySelector('.alert-card.recent, .new-badge, .badge'))) {
                            shouldReobserve = true;
                        }
                    }
                });
            }
        });
        
        if (shouldReobserve) {
            // Debounce re-observation to avoid excessive calls
            clearTimeout(window.animationReobserveTimeout);
            window.animationReobserveTimeout = setTimeout(observeAnimatedElements, 100);
        }
    });
    
    // Observe DOM changes
    mutationObserver.observe(document.body, {
        childList: true,
        subtree: true
    });
    
    // Cleanup on page unload
    window.addEventListener('beforeunload', function() {
        if (animationObserver) {
            animationObserver.disconnect();
        }
        mutationObserver.disconnect();
    });
})();
</script>
"""
)


def initialize_ui() -> None:
    """Initialize all UI related components and apply initial settings."""
    logger.info("Initializing UI and app state...")

    try:
        # Database and state managers are now initialized in main.py before this function
        # Apply initial theme setting (use default if not available)
        try:
            # Use state_manager to get app settings with proper initialization
            app_settings = dataobjects.state_manager.get_app_settings()
            dark_mode = True  # Default to dark mode
            theme_name = "dark"  # Default to dark theme
            if app_settings and hasattr(app_settings, "current_theme"):
                theme_name = app_settings.current_theme

            # Apply the theme
            apply_theme(theme_name)
            logger.info(f"Theme '{theme_name}' enabled based on settings.")
        except Exception as e:
            logger.error(f"Error applying theme: {str(e)}", exc_info=True)

        # Create the UI elements immediately
        create_ui_elements()
        logger.info("UI elements created.")

        # Start connector processing in a separate thread (not dependent on NiceGUI's event loop)
        import threading

        def start_connector_processing_thread():
            try:
                from . import connector_manager

                manager = connector_manager.get_manager()
                manager.start_connector_thread()
            except Exception as e:
                logger.error(
                    f"Error starting connector processing thread: {str(e)}",
                    exc_info=True,
                )

        # Start connector processing in background thread
        connector_thread = threading.Thread(
            target=start_connector_processing_thread, daemon=True
        )
        connector_thread.start()

        # Register cleanup handler
        app.on_shutdown(cleanup_resources)
        logger.info("Shutdown handler registered.")

        # Alert processor is already initialized in main.py, no need to initialize again
        logger.info("Alert processor already initialized in main.py startup sequence")

        # Schedule update manager initialization to happen after the server starts
        # and the first client connects, ensuring UI is fully rendered
        def init_update_manager():
            try:
                updater.update_manager.on_ui_ready()
                logger.info("Updater: UpdateManager scheduling initialized")
            except Exception as e:
                logger.error(
                    f"Updater: failed to initialize UpdateManager scheduling: {e}",
                    exc_info=True,
                )

        # Use a timer to delay initialization until after UI is fully loaded
        ui.timer(2.0, init_update_manager, once=True)

        logger.info("UI initialization completed, ready to start NiceGUI server.")

    except Exception as e:
        logger.error(f"Error during UI initialization: {str(e)}", exc_info=True)
        ui.notify(
            "Application initialization failed. Please restart.",
            type="negative",
            position="center",
        )
        raise


def _configure_webview2_for_admin():
    """Configure WebView2 to work properly when running as administrator (Windows only)"""
    import platform

    # Only configure WebView2 on Windows - skip on MacOS and Linux
    if platform.system() != "Windows":
        logger.debug("Skipping WebView2 configuration - not running on Windows")
        return

    try:
        import ctypes

        # Check if running as administrator (Windows-specific)
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except (AttributeError, OSError) as e:
            # Fallback for systems without IsUserAnAdmin or access issues
            logger.debug(f"Could not check admin status: {e}")
            is_admin = False

        if is_admin:
            logger.info(
                "Detected administrator privileges on Windows - configuring WebView2 data directory"
            )

            # Create a custom data directory for WebView2 that admin can access
            # Use the user's AppData directory instead of system temp
            appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "Mycelian")
            webview_data_dir = os.path.join(appdata_dir, "WebView2Data")

            # Create the directory if it doesn't exist
            os.makedirs(webview_data_dir, exist_ok=True)

            # Set WebView2 environment variables (Windows-specific)
            os.environ["WEBVIEW2_USER_DATA_FOLDER"] = webview_data_dir
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
                "--disable-web-security --disable-features=VizDisplayCompositor"
            )

            logger.info(f"WebView2 data directory set to: {webview_data_dir}")
        else:
            logger.debug(
                "Not running as administrator on Windows - using default WebView2 configuration"
            )

    except Exception as e:
        logger.warning(
            f"Failed to configure WebView2 for administrator on Windows: {e}"
        )
        # Don't fail the entire startup if this fails


def start_ui():
    """Start the NiceGUI server (blocking call)"""
    try:
        # Create UI elements if not already created (for phased initialization)
        if not _ui_elements_created:
            logger.info("Creating UI elements before starting server...")
            create_ui_elements()

            # Start connector processing in a separate thread (not dependent on NiceGUI's event loop)
            import threading

            def start_connector_processing_thread():
                try:
                    from . import connector_manager

                    manager = connector_manager.get_manager()
                    manager.start_connector_thread()
                except Exception as e:
                    logger.error(
                        f"Error starting connector processing thread: {str(e)}",
                        exc_info=True,
                    )

            # Start connector processing in background thread
            connector_thread = threading.Thread(
                target=start_connector_processing_thread, daemon=True
            )
            connector_thread.start()

            # Register cleanup handler
            app.on_shutdown(cleanup_resources)

        # Configure WebView2 data directory for administrator privileges
        _configure_webview2_for_admin()

        logger.info("Starting NiceGUI server...")
        ui.run(native=True, dark=True, reload=False)
        logger.info("NiceGUI app started.")
    except Exception as e:
        logger.error(f"Error starting NiceGUI server: {str(e)}", exc_info=True)
        raise


def cleanup_resources():
    """Clean up resources when the application is shutting down"""
    try:
        logger.debug("Cleaning up resources before shutdown")

        # Clean up web engine if it's running
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                web_engine.web_engine_instance.stop()
                logger.debug("Successfully stopped web engine")
        except Exception as e:
            logger.error(f"Error stopping web engine: {str(e)}")

        # Stop alert processor if it's running
        try:
            if hasattr(web_engine, "ALERTS_PAUSED"):
                web_engine.ALERTS_PAUSED = True
                logger.debug("Successfully paused alert processor")
        except Exception as e:
            logger.error(f"Error pausing alert processor: {str(e)}")

        # Stop the activity feed alert processor
        try:
            stop_alert_processor()
            logger.debug("Successfully stopped activity feed alert processor")
        except Exception as e:
            logger.error(f"Error stopping activity feed alert processor: {str(e)}")

        # Handle WebView2 cleanup gracefully to prevent BrowserProcessId errors
        try:
            _cleanup_webview2_gracefully()
        except Exception as e:
            logger.error(f"Error during WebView2 cleanup: {str(e)}")

        # Save statistics data before shutdown
        try:
            from . import statistics_manager

            logger.info("Saving statistics data before application shutdown...")
            statistics_manager.shutdown_statistics()
            logger.info("Statistics data saved successfully before shutdown")
        except Exception as e:
            logger.error(
                f"Error saving statistics during shutdown: {str(e)}", exc_info=True
            )

        logger.debug("Cleanup completed")

    except Exception as e:
        logger.error(f"Error during resource cleanup: {str(e)}")
        # Don't re-raise exceptions during cleanup


def _cleanup_webview2_gracefully():
    """Clean up WebView2/pywebview resources gracefully to prevent BrowserProcessId errors"""
    import platform

    try:
        # Try to access pywebview directly to close windows properly
        try:
            import webview

            if hasattr(webview, "windows") and webview.windows:
                for window in webview.windows:
                    try:
                        # Check if window and its core are still valid before accessing
                        if window and hasattr(window, "destroy"):
                            window.destroy()
                            logger.debug("WebView window destroyed gracefully")
                    except AttributeError as e:
                        # Handle platform-specific attribute errors
                        if platform.system() == "Windows":
                            # Windows-specific WebView2 error handling
                            if "BrowserProcessId" in str(e) or "NoneType" in str(e):
                                logger.warning(
                                    "WebView2 BrowserProcessId error handled during cleanup"
                                )
                            else:
                                logger.error(
                                    f"WebView2 cleanup attribute error: {str(e)}"
                                )
                        else:
                            # Generic error handling for other platforms
                            logger.error(f"WebView cleanup attribute error: {str(e)}")
                    except Exception as e:
                        logger.error(f"Error destroying WebView window: {str(e)}")
        except ImportError:
            logger.debug("pywebview not available for cleanup")
        except Exception as e:
            logger.error(f"Error accessing pywebview during cleanup: {str(e)}")

    except Exception as e:
        logger.error(f"Error during WebView graceful cleanup: {str(e)}")


class LazyTabPanel:
    """Tab panel that loads content lazily on first access"""

    def __init__(self, name: str, build_func: callable):
        self.name = name
        self.build_func = build_func
        self.loaded = False
        self.container = None
        self.spinner = None

    def ensure_loaded(self):
        """Load content if not already loaded"""
        if not self.loaded and self.container:
            # Clear the container and remove the spinner
            self.container.clear()

            # Load the actual content
            with self.container:
                self.build_func()

            self.loaded = True
            logger.debug(f"Lazy loaded tab: {self.name}")


def create_splash_screen():
    """Create splash screen shown during initialization"""
    global _splash_dialog, _splash_progress, _splash_status

    with ui.dialog(value=True) as _splash_dialog:
        _splash_dialog.props("persistent")
        _splash_dialog.classes("backdrop-blur-sm")

        with ui.card().classes("w-96 p-8 text-center bg-theme-surface"):
            # Logo/branding
            ui.label("Mycelian").classes("text-3xl font-bold mb-2 text-primary")
            ui.label("Streaming Toolkit").classes(
                "text-lg secondary-text mb-6"
            )

            # Progress bar
            _splash_progress = ui.linear_progress(value=0).classes("w-full mb-3")
            _splash_status = ui.label("Starting up...").classes(
                "text-sm secondary-text"
            )

    return _splash_dialog, _splash_progress, _splash_status


def update_splash_progress(value: float, text: str):
    """Update splash screen progress"""
    global _splash_progress, _splash_status
    if _splash_progress and _splash_status:
        _splash_progress.set_value(value)
        _splash_status.text = text


def close_splash_screen():
    """Close the splash screen"""
    global _splash_dialog
    if _splash_dialog:
        _splash_dialog.close()
        _splash_dialog = None


def initialize_ui_shell():
    """Initialize the UI shell (basic setup only) without creating elements"""
    logger.info("Initializing UI shell...")

    try:
        # Apply initial theme setting (use default if not available)
        try:
            # Use state_manager to get app settings with proper initialization
            app_settings = dataobjects.state_manager.get_app_settings()
            dark_mode = True  # Default to dark mode
            theme_name = "dark"  # Default to dark theme
            if app_settings and hasattr(app_settings, "current_theme"):
                theme_name = app_settings.current_theme

            # Apply the theme
            apply_theme(theme_name)
            logger.info(f"Theme '{theme_name}' enabled based on settings.")
        except Exception as e:
            logger.warning(
                f"Could not load theme setting, using default dark theme: {e}"
            )
            apply_theme("dark")  # Default to dark theme
            apply_theme(True)  # Default to dark mode

        # Create splash screen for deferred service loading
        create_splash_screen()
        update_splash_progress(0.1, "Core components loaded")

        # Don't create UI elements yet - they'll be created when start_ui() is called
        logger.info("UI shell initialized (elements deferred)")
    except Exception as e:
        logger.error(f"Error initializing UI shell: {str(e)}", exc_info=True)
        raise


def create_ui_elements():
    """Create all UI elements with lazy loading for non-critical tabs"""
    global _ui_elements_created
    if _ui_elements_created:
        return  # Already created

    # No header - help icons are in individual tabs

    # Create tabs at the top
    with ui.tabs().classes("w-full") as tabs:
        activity_tab = ui.tab("Activity Feed")
        alerts_tab = ui.tab("Alerts")
        source_settings_tab = ui.tab("Source Settings")
        source_controls_tab = ui.tab("Source Controls")
        connectors_tab = ui.tab("Connectors")
        chatbot_tab = ui.tab("Chatbot")
        settings_tab = ui.tab("Settings")

    # Main content area with tab panels - no overflow
    with ui.element("div").classes("main-content"):
        # Initialize lazy tabs dictionary
        lazy_tabs = {}

        with ui.tab_panels(tabs, value=activity_tab).classes(
            "w-full h-full flex-grow"
        ) as tab_panels:
            # Set references for help system context detection
            from .help_system.contextual_help import set_main_ui_references

            set_main_ui_references(tabs, tab_panels)
            # Activity Feed Tab - load immediately (it's the default view)
            with ui.tab_panel(activity_tab).classes("w-full h-full"):
                create_activity_feed_tab()

            # Other tabs - lazy load
            def build_alerts_tab():
                from .uiwindows.alertsettings import create_alert_settings_tab

                create_alert_settings_tab()

            def build_source_controls_tab():
                from .uiwindows.sourcecontrols import create_source_controls_tab

                create_source_controls_tab()

            def build_connectors_tab():
                from .uiwindows.connectors import create_connectors_tab

                create_connectors_tab()

            def build_chatbot_tab():
                from .uiwindows.chatbot import create_chatbot_tab

                create_chatbot_tab()

            def build_settings_tab():
                from .uiwindows.settings import create_settings_tab

                create_settings_tab()

            tab_definitions = [
                ("Alerts", build_alerts_tab, alerts_tab),
                ("Source Settings", create_custom_sources_tab, source_settings_tab),
                ("Source Controls", build_source_controls_tab, source_controls_tab),
                ("Connectors", build_connectors_tab, connectors_tab),
                ("Chatbot", build_chatbot_tab, chatbot_tab),
                ("Settings", build_settings_tab, settings_tab),
            ]

            for tab_name, build_func, tab_obj in tab_definitions:
                with ui.tab_panel(tab_obj).classes("w-full h-full") as panel:
                    lazy_tabs[tab_name] = LazyTabPanel(tab_name, build_func)
                    lazy_tabs[tab_name].container = panel
                    # Create spinner and store reference for later removal
                    spinner = ui.spinner("dots").classes("mx-auto").props("size=3rem")
                    lazy_tabs[tab_name].spinner = spinner

        # Add tab change handler for unsaved changes warning and lazy loading
        def on_main_tab_change(e):
            new_tab = e.value
            current_tab = tabs.value

            # Check if leaving the Settings tab with unsaved changes
            # tabs.value may be a string or object, compare appropriately
            def is_settings_tab(tab):
                if isinstance(tab, str):
                    return tab == "Settings"
                else:
                    return (
                        str(tab) == str(settings_tab)
                        or getattr(tab, "text", "") == "Settings"
                        or getattr(tab, "label", "") == "Settings"
                        or getattr(tab, "name", "") == "Settings"
                    )

            current_is_settings = is_settings_tab(current_tab)
            new_is_settings = is_settings_tab(new_tab)
            if current_is_settings and not new_is_settings:
                # Import here to avoid circular imports
                from .uiwindows.settings import settings_ui

                if settings_ui.has_unsaved_changes():
                    show_settings_unsaved_dialog(tabs, tab_panels, current_tab, new_tab)
                    # Prevent the tab switch by reverting the selection
                    tabs.value = current_tab
                    return

            # Allow the tab switch - set tabs.value to the new tab
            tabs.value = new_tab

            # Handle lazy loading for the new tab
            def get_tab_name(tab):
                if isinstance(tab, str):
                    return tab
                else:
                    return (
                        getattr(tab, "text", "")
                        or getattr(tab, "label", "")
                        or str(tab)
                    )

            new_tab_name = get_tab_name(new_tab)
            if new_tab_name in lazy_tabs:
                lazy_tabs[new_tab_name].ensure_loaded()

        # Monitor tab changes using a timer since tabs.on("change") may not work in native mode
        previous_tab = tabs.value

        def check_tab_changes():
            nonlocal previous_tab
            current_tab = tabs.value
            if current_tab != previous_tab:
                # The tab already changed, but we need to check if it should be allowed
                # Temporarily set tabs.value back to the old tab for the handler
                old_current = previous_tab
                tabs.value = old_current  # Set back to old tab
                # Create a mock event with the new tab
                mock_event = type("MockEvent", (), {"value": current_tab})()
                on_main_tab_change(mock_event)
                # The handler has now set tabs.value appropriately
                # Update previous_tab to the current value
                previous_tab = tabs.value

        ui.timer(0.5, check_tab_changes, active=True)  # Check every 500ms

        def show_settings_unsaved_dialog(
            tabs_component, tab_panels_component, current_tab, target_tab
        ):
            """Show dialog when leaving settings tab with unsaved changes."""
            with ui.dialog() as dialog, ui.card().classes("w-[420px] p-4"):
                ui.label("Unsaved changes").classes("text-lg font-bold mb-2")
                ui.label(
                    "You have unsaved changes in the Settings tab. Do you want to discard them and leave the Settings tab?"
                ).classes("secondary-text mb-4")

                def confirm_leave():
                    # Import here to avoid circular imports
                    from .uiwindows.settings import settings_ui

                    # Discard all unsaved changes in settings tabs
                    for tab in settings_ui._tabs_by_name.values():
                        if hasattr(tab, "dirty") and tab.dirty:
                            tab.discard()
                    # Switch to the target tab
                    tabs_component.value = target_tab
                    dialog.close()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Stay", on_click=dialog.close).props("outline")
                    ui.button("Discard and leave", on_click=confirm_leave).props(
                        "color=primary"
                    )

                dialog.open()  # Explicitly open the dialog

    # Mark UI elements as created
    _ui_elements_created = True


def reschedule_periodic_update_timer():
    # Backward-compat shim; delegate to centralized manager
    try:
        updater.update_manager.reschedule_periodic_timer()
    except Exception as e:
        logger.error(
            f"Updater: reschedule via UpdateManager failed: {e}", exc_info=True
        )


# Function to toggle alert status
def toggle_alerts():
    """Toggle the alert system pause state"""
    try:
        # Check if web engine instance is available
        if web_engine.web_engine_instance:
            # Use the web engine instance method
            web_engine.web_engine_instance.toggle_alerts()
            logger.debug("Toggled alerts via web engine instance")
        else:
            # Fallback: toggle the global flag directly
            web_engine.ALERTS_PAUSED = not web_engine.ALERTS_PAUSED
            logger.debug(
                f"Toggled alerts directly - ALERTS_PAUSED: {web_engine.ALERTS_PAUSED}"
            )

            # Try to broadcast via websocket if available
            def try_broadcast():
                if web_engine.web_engine_instance:
                    try:
                        web_engine.web_engine_instance.pause_status_update()
                        logger.debug("Broadcasted pause status update via websocket")
                        return True
                    except Exception as broadcast_error:
                        logger.debug(
                            f"Error broadcasting websocket update: {str(broadcast_error)}"
                        )
                        return False
                return False

            # Try immediate broadcast
            if not try_broadcast():
                # If web engine isn't ready, schedule a retry in 1 second
                logger.debug(
                    "Web engine not ready, scheduling retry broadcast in 1 second"
                )
                ui.timer(1.0, try_broadcast, once=True)

        # Force a sync of the pause button state in the activity feed
        try:
            from modules.uiwindows.activity_feed import sync_pause_button_state

            sync_pause_button_state()
        except Exception as sync_error:
            logger.debug(f"Could not sync pause button state: {str(sync_error)}")

        ui.update()

        # Show user feedback
        state_text = "paused" if web_engine.ALERTS_PAUSED else "resumed"
        ui.notify(f"Alerts {state_text}", type="info")

    except Exception as e:
        logger.error(f"Error toggling alerts: {str(e)}", exc_info=True)
        ui.notify("Error toggling alerts", type="negative")
