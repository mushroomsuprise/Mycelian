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
