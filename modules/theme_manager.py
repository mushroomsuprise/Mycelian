"""
Theme Manager for Mycelian Application

Provides a centralized theme system with CSS variables for consistent styling
across themes loaded from JSON files.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from .path_utils import get_data_path

logger = logging.getLogger(__name__)


@dataclass
class ThemeColors:
    """Theme color definitions"""

    # Meta information
    name: str = ""
    display_name: str = ""
    theme_type: str = ""

    # Primary colors
    primary: str = ""
    primary_hover: str = ""
    primary_light: str = ""

    # Background colors
    bg_base: str = ""
    bg_elevated: str = ""
    bg_surface: str = ""
    bg_overlay: str = ""

    # Text colors
    text_primary: str = ""
    text_secondary: str = ""
    text_muted: str = ""
    text_inverse: str = ""

    # Border colors
    border_default: str = ""
    border_subtle: str = ""
    border_accent: str = ""

    # Status colors
    success: str = ""
    warning: str = ""
    error: str = ""
    info: str = ""

    # Interactive states
    hover_overlay: str = ""
    active_overlay: str = ""
    focus_ring: str = ""


def generate_css_variables(theme: ThemeColors) -> str:
    """Generate CSS custom properties from theme colors"""
    return f"""
:root, body, html {{
    /* Meta */
    --theme-name: {theme.name};
    --theme-type: {theme.theme_type};

    /* Primary */
    --color-primary: {theme.primary};
    --color-primary-hover: {theme.primary_hover};
    --color-primary-light: {theme.primary_light};

    /* Backgrounds */
    --color-bg-base: {theme.bg_base};
    --color-bg-elevated: {theme.bg_elevated};
    --color-bg-surface: {theme.bg_surface};
    --color-bg-overlay: {theme.bg_overlay};

    /* Text */
    --color-text-primary: {theme.text_primary};
    --color-text-secondary: {theme.text_secondary};
    --color-text-muted: {theme.text_muted};
    --color-text-inverse: {theme.text_inverse};

    /* Borders */
    --color-border-default: {theme.border_default};
    --color-border-subtle: {theme.border_subtle};
    --color-border-accent: {theme.border_accent};

    /* Status */
    --color-success: {theme.success};
    --color-warning: {theme.warning};
    --color-error: {theme.error};
    --color-info: {theme.info};

    /* Interactive */
    --color-hover-overlay: {theme.hover_overlay};
    --color-active-overlay: {theme.active_overlay};
    --color-focus-ring: {theme.focus_ring};
}}
"""


def generate_preview_css_variables(theme: ThemeColors) -> str:
    """Generate CSS custom properties specifically for theme preview (separate from applied theme)"""
    return f"""
.theme-preview-container {{
    /* Meta */
    --preview-theme-name: {theme.name};
    --preview-theme-type: {theme.theme_type or 'dark'};

    /* Primary */
    --preview-color-primary: {theme.primary or '#1976d2'};
    --preview-color-primary-hover: {theme.primary_hover or '#1565c0'};
    --preview-color-primary-light: {theme.primary_light or 'rgba(25, 118, 210, 0.15)'};

    /* Backgrounds */
    --preview-color-bg-base: {theme.bg_base or '#121212'};
    --preview-color-bg-elevated: {theme.bg_elevated or '#1e1e1e'};
    --preview-color-bg-surface: {theme.bg_surface or '#262626'};
    --preview-color-bg-overlay: {theme.bg_overlay or 'rgba(0, 0, 0, 0.5)'};

    /* Text */
    --preview-color-text-primary: {theme.text_primary or '#ffffff'};
    --preview-color-text-secondary: {theme.text_secondary or '#b3b3b3'};
    --preview-color-text-muted: {theme.text_muted or '#666666'};
    --preview-color-text-inverse: {theme.text_inverse or '#000000'};

    /* Borders */
    --preview-color-border-default: {theme.border_default or '#333333'};
    --preview-color-border-subtle: {theme.border_subtle or '#444444'};
    --preview-color-border-accent: {theme.border_accent or '#1976d2'};

    /* Status */
    --preview-color-success: {theme.success or '#4caf50'};
    --preview-color-warning: {theme.warning or '#ff9800'};
    --preview-color-error: {theme.error or '#f44336'};
    --preview-color-info: {theme.info or '#2196f3'};

    /* Interactive */
    --preview-color-hover-overlay: {theme.hover_overlay or 'rgba(255, 255, 255, 0.05)'};
    --preview-color-active-overlay: {theme.active_overlay or 'rgba(255, 255, 255, 0.1)'};
    --preview-color-focus-ring: {theme.focus_ring or 'rgba(25, 118, 210, 0.5)'};
}}
"""


# Help browser and contextual help UI — uses :root vars from generate_css_variables()
HELP_SYSTEM_CSS = """
/* Help Browser Dialog */
.help-browser-dialog .q-card {
    background: var(--color-bg-base) !important;
    border: 1px solid var(--color-border-accent) !important;
    border-radius: 12px !important;
}

.help-card-root {
    background: var(--color-bg-base) !important;
}

.help-sidebar {
    background: var(--color-bg-elevated) !important;
    border-right: 1px solid var(--color-border-default) !important;
}

.help-sidebar-header {
    background: linear-gradient(
        135deg,
        var(--color-primary-light) 0%,
        color-mix(in srgb, var(--color-primary-light) 40%, transparent) 100%
    ) !important;
    border-bottom: 1px solid var(--color-border-default) !important;
}

.help-search-input .q-field__control {
    background: var(--color-hover-overlay) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 8px !important;
}

.help-search-input .q-field__control:hover {
    border-color: color-mix(in srgb, var(--color-primary) 45%, var(--color-border-default)) !important;
}

.help-search-input .q-field__control:focus-within {
    border-color: var(--color-primary) !important;
    box-shadow: 0 0 0 2px var(--color-focus-ring) !important;
}

.help-search-input .q-field__native,
.help-search-input input,
.help-search-input textarea {
    color: var(--color-text-primary) !important;
}

.help-category-btn {
    background: color-mix(in srgb, var(--color-hover-overlay) 80%, transparent) !important;
    border: 1px solid var(--color-border-subtle) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    margin-bottom: 4px !important;
}

.help-category-btn:hover {
    background: var(--color-primary-light) !important;
    border-color: color-mix(in srgb, var(--color-border-accent) 70%, var(--color-border-subtle)) !important;
}

.help-category-btn.expanded {
    background: color-mix(in srgb, var(--color-primary-light) 90%, var(--color-bg-surface)) !important;
    border-color: var(--color-border-accent) !important;
}

.help-topic-btn {
    background: transparent !important;
    border-left: 2px solid transparent !important;
    border-radius: 0 6px 6px 0 !important;
    transition: all 0.15s ease !important;
    padding: 8px 12px 8px 16px !important;
    margin: 2px 0 !important;
}

.help-topic-btn:hover {
    background: color-mix(in srgb, var(--color-primary-light) 60%, transparent) !important;
    border-left-color: color-mix(in srgb, var(--color-primary) 55%, transparent) !important;
}

.help-topic-btn.active {
    background: color-mix(in srgb, var(--color-primary-light) 85%, transparent) !important;
    border-left-color: var(--color-primary) !important;
}

.help-content-area {
    background: var(--color-bg-surface) !important;
}

.help-toolbar {
    background: var(--color-hover-overlay) !important;
    border-bottom: 1px solid var(--color-border-subtle) !important;
}

.help-breadcrumb {
    background: color-mix(in srgb, var(--color-hover-overlay) 70%, transparent) !important;
    border-radius: 6px !important;
    padding: 8px 12px !important;
}

.help-breadcrumb-item {
    color: var(--color-text-muted) !important;
    transition: color 0.15s ease !important;
}

.help-breadcrumb-item:hover {
    color: var(--color-primary) !important;
}

.help-breadcrumb-separator {
    color: var(--color-text-muted) !important;
    margin: 0 8px !important;
}

.help-breadcrumb-current {
    color: var(--color-text-primary) !important;
}

.help-topic-card {
    background: color-mix(in srgb, var(--color-bg-surface) 85%, var(--color-bg-elevated)) !important;
    border: 1px solid var(--color-border-subtle) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    cursor: pointer !important;
}

.help-topic-card:hover {
    background: var(--color-bg-elevated) !important;
    border-color: color-mix(in srgb, var(--color-border-accent) 65%, var(--color-border-subtle)) !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 12px var(--color-bg-overlay) !important;
}

.help-category-badge {
    background: var(--color-primary-light) !important;
    color: var(--color-primary) !important;
    border: 1px solid color-mix(in srgb, var(--color-border-accent) 80%, transparent) !important;
    border-radius: 4px !important;
    font-size: 0.7rem !important;
    padding: 2px 8px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}

.help-browser-dialog .help-category-count-badge.q-badge {
    background: var(--color-primary-light) !important;
    color: var(--color-primary) !important;
    border: 1px solid var(--color-border-accent) !important;
    font-size: 0.65rem !important;
}

.help-markdown-content,
.help-accordion-markdown {
    color: var(--color-text-secondary) !important;
    line-height: 1.7 !important;
}

.help-markdown-content h1,
.help-markdown-content h2,
.help-markdown-content h3,
.help-markdown-content h4,
.help-accordion-markdown h1,
.help-accordion-markdown h2,
.help-accordion-markdown h3,
.help-accordion-markdown h4 {
    color: var(--color-text-primary) !important;
    margin-top: 1.5em !important;
    margin-bottom: 0.75em !important;
}

.help-markdown-content h1,
.help-accordion-markdown h1 { font-size: 1.75rem !important; }
.help-markdown-content h2,
.help-accordion-markdown h2 { font-size: 1.4rem !important; }
.help-markdown-content h3,
.help-accordion-markdown h3 { font-size: 1.2rem !important; }

.help-markdown-content p,
.help-accordion-markdown p {
    margin-bottom: 1em !important;
}

.help-markdown-content ul,
.help-markdown-content ol,
.help-accordion-markdown ul,
.help-accordion-markdown ol {
    margin-left: 1.5em !important;
    margin-bottom: 1em !important;
}

.help-markdown-content li,
.help-accordion-markdown li {
    margin-bottom: 0.5em !important;
}

.help-markdown-content code,
.help-accordion-markdown code {
    background: var(--color-primary-light) !important;
    color: var(--color-text-primary) !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-family: 'Consolas', 'Monaco', monospace !important;
}

.help-markdown-content pre,
.help-accordion-markdown pre {
    background: var(--color-bg-elevated) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 8px !important;
    padding: 16px !important;
    overflow-x: auto !important;
}

.help-markdown-content pre code,
.help-accordion-markdown pre code {
    background: transparent !important;
    padding: 0 !important;
}

.help-markdown-content a,
.help-accordion-markdown a {
    color: var(--color-info) !important;
    text-decoration: none !important;
    border-bottom: 1px solid transparent !important;
    transition: all 0.15s ease !important;
}

.help-markdown-content a:hover,
.help-accordion-markdown a:hover {
    color: var(--color-primary-hover) !important;
    border-bottom-color: var(--color-info) !important;
}

.help-markdown-content blockquote,
.help-accordion-markdown blockquote {
    border-left: 3px solid var(--color-primary) !important;
    background: color-mix(in srgb, var(--color-primary-light) 50%, transparent) !important;
    padding: 12px 16px !important;
    margin: 1em 0 !important;
    border-radius: 0 6px 6px 0 !important;
}

.help-markdown-content table,
.help-accordion-markdown table {
    width: 100% !important;
    border-collapse: collapse !important;
    margin: 1em 0 !important;
}

.help-markdown-content th,
.help-markdown-content td,
.help-accordion-markdown th,
.help-accordion-markdown td {
    border: 1px solid var(--color-border-default) !important;
    padding: 8px 12px !important;
}

.help-markdown-content th,
.help-accordion-markdown th {
    background: var(--color-primary-light) !important;
    font-weight: 600 !important;
}

.help-search-result {
    background: color-mix(in srgb, var(--color-bg-surface) 75%, var(--color-bg-elevated)) !important;
    border: 1px solid var(--color-border-subtle) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
    margin-bottom: 4px !important;
}

.help-search-result:hover {
    background: color-mix(in srgb, var(--color-primary-light) 45%, var(--color-bg-surface)) !important;
    border-color: color-mix(in srgb, var(--color-border-accent) 55%, var(--color-border-subtle)) !important;
}

.help-nav-btn {
    background: var(--color-hover-overlay) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
}

.help-nav-btn:hover {
    background: var(--color-primary-light) !important;
    border-color: var(--color-border-accent) !important;
}

.help-nav-btn:disabled {
    opacity: 0.4 !important;
}

.help-close-btn {
    background: var(--color-hover-overlay) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
}

.help-close-btn:hover {
    background: color-mix(in srgb, var(--color-error) 22%, transparent) !important;
    color: var(--color-error) !important;
}

.help-quick-card {
    background: linear-gradient(
        135deg,
        color-mix(in srgb, var(--color-primary-light) 90%, transparent) 0%,
        color-mix(in srgb, var(--color-primary-light) 25%, transparent) 100%
    ) !important;
    border: 1px solid color-mix(in srgb, var(--color-border-accent) 65%, transparent) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    cursor: pointer !important;
}

.help-quick-card:hover {
    background: linear-gradient(
        135deg,
        var(--color-primary-light) 0%,
        color-mix(in srgb, var(--color-primary-light) 55%, transparent) 100%
    ) !important;
    border-color: var(--color-border-accent) !important;
    transform: translateY(-2px) !important;
}

.help-stat-card {
    background: color-mix(in srgb, var(--color-bg-surface) 70%, var(--color-bg-elevated)) !important;
    border: 1px solid var(--color-border-subtle) !important;
    border-radius: 8px !important;
}

.help-related-btn {
    background: var(--color-hover-overlay) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 16px !important;
    transition: all 0.15s ease !important;
    font-size: 0.85rem !important;
}

.help-related-btn:hover {
    background: var(--color-primary-light) !important;
    border-color: var(--color-border-accent) !important;
}

.help-scroll::-webkit-scrollbar {
    width: 6px !important;
}

.help-scroll::-webkit-scrollbar-track {
    background: color-mix(in srgb, var(--color-border-subtle) 40%, transparent) !important;
}

.help-scroll::-webkit-scrollbar-thumb {
    background: color-mix(in srgb, var(--color-primary) 35%, transparent) !important;
    border-radius: 3px !important;
}

.help-scroll::-webkit-scrollbar-thumb:hover {
    background: color-mix(in srgb, var(--color-primary) 55%, transparent) !important;
}

/* Semantic text / icons (Python uses these instead of hardcoded colors) */
.help-text-primary { color: var(--color-text-primary) !important; }
.help-text-secondary { color: var(--color-text-secondary) !important; }
.help-text-muted { color: var(--color-text-muted) !important; }
.help-stat-value-primary { color: var(--color-primary) !important; }
.help-stat-value-success { color: var(--color-success) !important; }
.help-icon-accent { color: var(--color-primary) !important; }
.help-icon-accent-muted {
    color: color-mix(in srgb, var(--color-primary) 70%, transparent) !important;
}
.help-expand-icon {
    color: var(--color-text-muted) !important;
    transition: transform 0.2s ease !important;
}
.help-sidebar-topic-title {
    color: var(--color-text-secondary) !important;
    max-width: 200px !important;
}
.help-browser-dialog .help-separator-accent.q-separator,
.help-browser-dialog .help-separator-accent {
    background: color-mix(in srgb, var(--color-primary) 35%, transparent) !important;
}

.help-browser-dialog .help-separator-subtle.q-separator,
.help-browser-dialog .help-separator-subtle {
    background: var(--color-border-subtle) !important;
}
.help-flat-link {
    color: var(--color-primary) !important;
}

/* Contextual help (inline, cards) */
.help-inline-icon {
    color: var(--color-info) !important;
    flex-shrink: 0 !important;
}

.help-inline-link,
.help-inline-link .q-btn__content {
    color: var(--color-info) !important;
}

.help-inline-link:hover,
.help-inline-link:hover .q-btn__content {
    color: var(--color-primary-hover) !important;
}

.help-contextual-card {
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-subtle) !important;
    border-radius: 8px !important;
    transition: background-color 0.2s ease, border-color 0.2s ease !important;
}

.help-contextual-card:hover {
    background: var(--color-bg-elevated) !important;
    border-color: var(--color-border-default) !important;
}
"""


class ThemeManager:
    """Manages application theme state and CSS generation"""

    _instance = None
    _current_theme: str = "dark"
    _loaded_themes: Dict[str, ThemeColors] = {}
    _themes_dir: Optional[Path] = None

    def __init__(self):
        if ThemeManager._themes_dir is None:
            ThemeManager._themes_dir = Path(get_data_path("themes"))

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    def load_themes_from_directory(self) -> List[Tuple[str, str]]:
        """Discover and load all JSON theme files from themes directory.

        Returns:
            List of (name, display_name) tuples for all loaded themes
        """
        self._loaded_themes.clear()

        if self._themes_dir is None:
            return []

        if not self._themes_dir.exists():
            self._themes_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created themes directory: {self._themes_dir}")

        # Scan for JSON files
        theme_files = list(self._themes_dir.glob("*.json"))

        # Also scan custom subdirectory if it exists
        custom_dir = self._themes_dir / "custom"
        if custom_dir.exists():
            theme_files.extend(custom_dir.glob("*.json"))

        for theme_file in theme_files:
            try:
                theme = self._load_theme_from_file(theme_file)
                if theme and theme.name:
                    self._loaded_themes[theme.name] = theme
                    logger.info(
                        f"Loaded theme: {theme.display_name} from {theme_file.name}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load theme from {theme_file}: {e}")

        # Return sorted list of themes
        return [
            (name, theme.display_name)
            for name, theme in sorted(self._loaded_themes.items())
        ]

    def _load_theme_from_file(self, theme_file: Path) -> Optional[ThemeColors]:
        """Load a single theme from JSON file.

        Returns:
            ThemeColors instance or None if loading fails
        """
        try:
            with open(theme_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Create ThemeColors from JSON data
            return ThemeColors(**data)
        except Exception as e:
            logger.error(f"Error loading theme file {theme_file}: {e}")
            return None

    def get_available_themes(self) -> List[Tuple[str, str]]:
        """Get list of all available themes.

        Returns:
            List of (name, display_name) tuples
        """
        if not self._loaded_themes:
            self.load_themes_from_directory()

        return [
            (name, theme.display_name)
            for name, theme in sorted(self._loaded_themes.items())
        ]

    def get_theme_by_name(self, theme_name: str) -> Optional[ThemeColors]:
        """Get theme by name, with fallback to dark theme.

        Returns:
            ThemeColors instance or dark theme as fallback
        """
        theme = self._loaded_themes.get(theme_name)

        if theme is None:
            logger.warning(f"Theme '{theme_name}' not found, falling back to 'dark'")
            theme = self._loaded_themes.get("dark")

            # If dark theme also doesn't exist, load themes again
            if theme is None:
                self.load_themes_from_directory()
                theme = self._loaded_themes.get("dark")

        return theme

    def get_theme_display_name(self, theme_name: str) -> str:
        """Get display name for a theme.

        Returns:
            Display name or theme_name if not found
        """
        theme = self._loaded_themes.get(theme_name)
        return theme.display_name if theme else theme_name

    def get_theme_type(self, theme_name: str) -> str:
        """Get theme type (dark/light) for a theme.

        Returns:
            'dark', 'light', or 'dark' as fallback
        """
        theme = self._loaded_themes.get(theme_name)
        return theme.theme_type if theme else "dark"

    def get_theme(self) -> ThemeColors:
        """Get current theme colors"""
        theme = self.get_theme_by_name(self._current_theme)
        return (
            theme if theme is not None else ThemeColors()
        )  # Return default theme if None

    def set_theme(self, theme_name: str):
        """Set current theme by name"""
        if not self._loaded_themes:
            self.load_themes_from_directory()

        if theme_name not in self._loaded_themes:
            logger.warning(f"Theme '{theme_name}' not found, falling back to 'dark'")
            self._current_theme = "dark"
        else:
            self._current_theme = theme_name
            logger.info(f"Theme changed to: {theme_name}")

    def theme_name_exists(self, theme_name: str) -> bool:
        """Check if a theme with the given name already exists.

        Returns:
            True if theme name exists, False otherwise
        """
        if not self._loaded_themes:
            self.load_themes_from_directory()
        return theme_name in self._loaded_themes

    def get_default_colors_for_type(self, theme_type: str) -> dict:
        """Get default color values for a specific theme type.

        Args:
            theme_type: 'dark' or 'light'

        Returns:
            Dictionary with default color values
        """
        if theme_type == "light":
            return {
                "primary": "rgb(100, 0, 230)",
                "primary_hover": "rgb(85, 0, 200)",
                "primary_light": "rgba(100, 0, 230, 0.1)",
                "bg_base": "rgb(245, 245, 250)",
                "bg_elevated": "rgb(255, 255, 255)",
                "bg_surface": "rgb(240, 240, 245)",
                "bg_overlay": "rgba(0, 0, 0, 0.3)",
                "text_primary": "rgb(24, 24, 32)",
                "text_secondary": "rgba(24, 24, 32, 0.8)",
                "text_muted": "rgba(24, 24, 32, 0.5)",
                "text_inverse": "rgb(255, 255, 255)",
                "border_default": "rgba(0, 0, 0, 0.12)",
                "border_subtle": "rgba(0, 0, 0, 0.06)",
                "border_accent": "rgba(100, 0, 230, 0.3)",
                "success": "rgb(22, 163, 74)",
                "warning": "rgb(202, 138, 4)",
                "error": "rgb(220, 38, 38)",
                "info": "rgb(37, 99, 235)",
                "hover_overlay": "rgba(0, 0, 0, 0.04)",
                "active_overlay": "rgba(0, 0, 0, 0.08)",
                "focus_ring": "rgba(100, 0, 230, 0.4)",
            }
        else:  # dark theme defaults
            return {
                "primary": "rgb(115, 0, 255)",
                "primary_hover": "rgb(135, 30, 255)",
                "primary_light": "rgba(115, 0, 255, 0.15)",
                "bg_base": "rgb(18, 18, 24)",
                "bg_elevated": "rgb(25, 25, 35)",
                "bg_surface": "rgb(35, 35, 45)",
                "bg_overlay": "rgba(0, 0, 0, 0.5)",
                "text_primary": "rgb(240, 240, 255)",
                "text_secondary": "rgba(240, 240, 255, 0.8)",
                "text_muted": "rgba(200, 200, 220, 0.6)",
                "text_inverse": "rgb(18, 18, 24)",
                "border_default": "rgba(255, 255, 255, 0.1)",
                "border_subtle": "rgba(255, 255, 255, 0.05)",
                "border_accent": "rgba(115, 0, 255, 0.3)",
                "success": "rgb(34, 197, 94)",
                "warning": "rgb(234, 179, 8)",
                "error": "rgb(239, 68, 68)",
                "info": "rgb(59, 130, 246)",
                "hover_overlay": "rgba(255, 255, 255, 0.05)",
                "active_overlay": "rgba(255, 255, 255, 0.1)",
                "focus_ring": "rgba(115, 0, 255, 0.5)",
            }

    def _theme_to_dict(self, theme: ThemeColors) -> dict:
        """Convert a ThemeColors instance to a JSON-serializable dictionary.

        Args:
            theme: ThemeColors instance

        Returns:
            Dictionary of theme data
        """
        return {
            "name": theme.name,
            "display_name": theme.display_name or theme.name,
            "theme_type": theme.theme_type or "dark",
            "primary": theme.primary,
            "primary_hover": theme.primary_hover,
            "primary_light": theme.primary_light,
            "bg_base": theme.bg_base,
            "bg_elevated": theme.bg_elevated,
            "bg_surface": theme.bg_surface,
            "bg_overlay": theme.bg_overlay,
            "text_primary": theme.text_primary,
            "text_secondary": theme.text_secondary,
            "text_muted": theme.text_muted,
            "text_inverse": theme.text_inverse,
            "border_default": theme.border_default,
            "border_subtle": theme.border_subtle,
            "border_accent": theme.border_accent,
            "success": theme.success,
            "warning": theme.warning,
            "error": theme.error,
            "info": theme.info,
            "hover_overlay": theme.hover_overlay,
            "active_overlay": theme.active_overlay,
            "focus_ring": theme.focus_ring,
        }

    def _find_theme_file(self, theme_name: str) -> Optional[Path]:
        """Find the file path for an existing theme by name.

        Searches both the root themes directory and the custom subdirectory.

        Args:
            theme_name: Name of the theme to find

        Returns:
            Path to the theme file, or None if not found
        """
        if self._themes_dir is None:
            return None

        # Check root themes directory
        root_file = self._themes_dir / f"{theme_name}.json"
        if root_file.exists():
            return root_file

        # Also check with _theme suffix (e.g. dark_theme.json for name "dark")
        root_file_suffixed = self._themes_dir / f"{theme_name}_theme.json"
        if root_file_suffixed.exists():
            return root_file_suffixed

        # Check custom subdirectory
        custom_file = self._themes_dir / "custom" / f"{theme_name}.json"
        if custom_file.exists():
            return custom_file

        return None

    def save_custom_theme(self, theme: ThemeColors) -> bool:
        """Save a new custom theme to the custom themes directory.

        Args:
            theme: ThemeColors instance to save

        Returns:
            True if saved successfully, False otherwise
        """
        if self._themes_dir is None:
            logger.error("Themes directory not initialized")
            return False

        # Validate theme name
        if not theme.name or not theme.name.strip():
            logger.error("Theme name cannot be empty")
            return False

        # Check for existing theme names
        if self.theme_name_exists(theme.name):
            logger.error(f"Theme '{theme.name}' already exists")
            return False

        # Create custom themes directory if it doesn't exist
        custom_dir = self._themes_dir / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)

        # Create theme file path
        theme_file = custom_dir / f"{theme.name}.json"

        try:
            theme_data = self._theme_to_dict(theme)

            # Write theme to JSON file
            with open(theme_file, "w", encoding="utf-8") as f:
                json.dump(theme_data, f, indent=2, ensure_ascii=False)

            # Add to loaded themes
            self._loaded_themes[theme.name] = theme
            logger.info(f"Successfully saved custom theme: {theme.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to save theme '{theme.name}': {e}")
            return False

    def update_custom_theme(self, theme: ThemeColors) -> bool:
        """Update an existing theme file with new color values.

        Finds the existing theme file (in themes/ or themes/custom/) and
        overwrites it with the updated data.

        Args:
            theme: ThemeColors instance with updated values

        Returns:
            True if updated successfully, False otherwise
        """
        if self._themes_dir is None:
            logger.error("Themes directory not initialized")
            return False

        if not theme.name or not theme.name.strip():
            logger.error("Theme name cannot be empty")
            return False

        # Find the existing file
        theme_file = self._find_theme_file(theme.name)
        if theme_file is None:
            logger.error(f"Theme file for '{theme.name}' not found")
            return False

        try:
            theme_data = self._theme_to_dict(theme)

            with open(theme_file, "w", encoding="utf-8") as f:
                json.dump(theme_data, f, indent=2, ensure_ascii=False)

            # Update in-memory cache
            self._loaded_themes[theme.name] = theme
            logger.info(f"Successfully updated theme: {theme.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to update theme '{theme.name}': {e}")
            return False


# Singleton accessor
def get_theme_manager() -> ThemeManager:
    return ThemeManager.get_instance()
