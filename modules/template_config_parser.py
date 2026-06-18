#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

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

import copy
import glob
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from .path_utils import get_data_path

logger = logging.getLogger(__name__)


def _overlay_source_control_runtime_state(dynamic_out: Dict[str, Any]) -> None:
    """
    Mutate dynamic_out elements: when ``persist`` includes ``runtime_database_path``
    and ``runtime_state_key``, set ``value`` from ``database_manager.get_data(path)``
    if that key exists. Paths are fetched at most once per distinct path.
    """
    elements = dynamic_out.get("elements")
    if not isinstance(elements, list):
        return
    try:
        from . import database_manager
    except Exception as e:
        logger.debug("Skipping runtime source-control overlay (import): %s", e)
        return

    path_cache: Dict[str, Dict[str, Any]] = {}

    def _state_for_path(path: str) -> Dict[str, Any]:
        if path in path_cache:
            return path_cache[path]
        try:
            raw = database_manager.get_data(path)
        except Exception as e:
            logger.debug("Runtime overlay get_data failed for %s: %s", path, e)
            path_cache[path] = {}
            return path_cache[path]
        path_cache[path] = raw if isinstance(raw, dict) else {}
        return path_cache[path]

    for merged in elements:
        if not isinstance(merged, dict):
            continue
        persist = merged.get("persist")
        if not isinstance(persist, dict):
            continue
        db_path = persist.get("runtime_database_path")
        state_key = persist.get("runtime_state_key")
        if not db_path or not isinstance(db_path, str) or not state_key or not isinstance(
            state_key, str
        ):
            continue
        db_path = db_path.strip()
        state_key = state_key.strip()
        if not db_path or not state_key:
            continue
        state = _state_for_path(db_path)
        if state_key in state:
            merged["value"] = state[state_key]


def resolve_dynamic_control_values_from_elements(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a deep copy of ``dynamic_controls`` with display ``value`` taken from the
    main ``elements`` entry identified by ``persist.target_element_id`` when present,
    matching what ``persist_template_control_change`` writes. Then, when ``persist``
    includes ``runtime_database_path`` and ``runtime_state_key``, overlay ``value``
    from that database document (see ``_overlay_source_control_runtime_state``).
    Otherwise each control keeps its JSON defaults.
    """
    if not isinstance(config, dict):
        return {}
    dc = config.get("dynamic_controls")
    if not isinstance(dc, dict):
        return {}
    out = copy.deepcopy(dc)
    elements = out.get("elements")
    if not isinstance(elements, list):
        return out

    id_to_main: Dict[Any, Dict[str, Any]] = {}
    for el in config.get("elements", []):
        if isinstance(el, dict) and el.get("id") is not None:
            id_to_main[el["id"]] = el

    for merged in elements:
        if not isinstance(merged, dict):
            continue
        persist = merged.get("persist")
        if not isinstance(persist, dict):
            continue
        target_id = persist.get("target_element_id")
        if not target_id:
            continue
        main_el = id_to_main.get(target_id)
        if isinstance(main_el, dict) and "value" in main_el:
            merged["value"] = main_el["value"]

    _overlay_source_control_runtime_state(out)

    return out


class TemplateConfigParser:
    """
    Parser for template configuration files.
    Handles reading, editing, and saving JSON configuration files.
    """
    
    def __init__(self, config_dir='templates/template_configs'):
        """
        Initialize the TemplateConfigParser
        
        Args:
            config_dir (str): Directory containing JSON configuration files
        """
        # Use path utils to get correct config directory for exe
        if config_dir == 'templates/template_configs':
            self.config_dir = get_data_path('templates/template_configs')
        else:
            self.config_dir = config_dir
        
        # Create config directory if it doesn't exist
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
            logger.debug(f"Created config directory: {self.config_dir}")

        # mtime-keyed caches (invalidated on save/delete/create)
        self._file_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._hidden_cache: Dict[str, Tuple[float, bool]] = {}
        self._cache_lock = threading.Lock()

    def _config_mtime(self, config_name: str) -> float:
        path = self.get_config_path(config_name)
        try:
            return os.path.getmtime(path) if os.path.exists(path) else 0.0
        except OSError:
            return 0.0

    def invalidate_cache(self, config_name: Optional[str] = None) -> None:
        """Drop cached JSON for one config or all configs."""
        with self._cache_lock:
            if config_name is None:
                self._file_cache.clear()
                self._hidden_cache.clear()
            else:
                self._file_cache.pop(config_name, None)
                self._hidden_cache.pop(config_name, None)

    def _load_raw_config_from_disk(self, config_name: str) -> Dict[str, Any]:
        """Read JSON from disk without using the mtime cache."""
        config_path = self.get_config_path(config_name)
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return loaded
            return self._create_default_config(config_name)
        default_config = self._create_default_config(config_name)
        self.save_config(config_name, default_config)
        return default_config

    def _get_cached_raw_config(self, config_name: str) -> Dict[str, Any]:
        mtime = self._config_mtime(config_name)
        with self._cache_lock:
            cached = self._file_cache.get(config_name)
            if cached is not None and cached[0] == mtime:
                return copy.deepcopy(cached[1])
        raw = self._load_raw_config_from_disk(config_name)
        with self._cache_lock:
            self._file_cache[config_name] = (self._config_mtime(config_name), copy.deepcopy(raw))
        return raw

    def _filter_config(
        self,
        config: Dict[str, Any],
        include_dynamic_controls: bool,
        include_streamdeck_options: bool,
    ) -> Dict[str, Any]:
        out = config
        if (
            not include_dynamic_controls
            and isinstance(out, dict)
            and "dynamic_controls" in out
        ):
            out = out.copy()
            del out["dynamic_controls"]
        if (
            not include_streamdeck_options
            and isinstance(out, dict)
            and "streamdeck_options" in out
        ):
            if out is config:
                out = out.copy()
            del out["streamdeck_options"]
        return out

    def get_config_files(self) -> List[str]:
        """
        Get a list of all configuration files
        
        Returns:
            List[str]: List of config filenames without extension
        """
        if not os.path.exists(self.config_dir):
            logger.warning(f"Config directory {self.config_dir} not found.")
            return []
        
        # Spore Studio editor sidecars use a ``.spore.json`` suffix and
        # used to live alongside the public configs here, leaking into
        # the Source Settings dropdown as e.g. ``title.spore``. They now
        # live under ``templates/_spore/`` (see
        # ``modules/spore_studio/template_parser_back.py``); this filter
        # is a defensive safety net so a stray sidecar in the wrong dir
        # never resurfaces in the UI.
        json_files = [
            f for f in glob.glob(os.path.join(self.config_dir, '*.json'))
            if not f.endswith('.spore.json')
        ]
        config_names = [os.path.basename(f).replace('.json', '') for f in json_files]
        logger.debug(f"Found {len(config_names)} config files in {self.config_dir}")
        return config_names
    
    def get_non_hidden_config_files(self) -> List[str]:
        """
        Get a list of all non-hidden configuration files
        
        Returns:
            List[str]: List of non-hidden config filenames without extension
        """
        all_configs = self.get_config_files()
        non_hidden_configs = []
        
        for config_name in all_configs:
            if not self.is_config_hidden(config_name):
                non_hidden_configs.append(config_name)
        
        logger.debug(f"Found {len(non_hidden_configs)} non-hidden config files out of {len(all_configs)} total")
        return non_hidden_configs
    
    def is_config_hidden(self, config_name: str) -> bool:
        """
        Check if a configuration is marked as hidden
        
        Args:
            config_name (str): Name of the config (without extension)
            
        Returns:
            bool: True if the config is hidden, False otherwise
        """
        try:
            mtime = self._config_mtime(config_name)
            with self._cache_lock:
                cached = self._hidden_cache.get(config_name)
                if cached is not None and cached[0] == mtime:
                    return cached[1]
            config = self.load_config(
                config_name, include_dynamic_controls=False
            )
            hidden = bool(
                isinstance(config, dict) and config.get("hidden", False)
            )
            with self._cache_lock:
                self._hidden_cache[config_name] = (mtime, hidden)
            if hidden:
                logger.debug("Config %s is marked as hidden", config_name)
            return hidden
        except Exception as e:
            logger.error(
                "Error checking if config %s is hidden: %s",
                config_name,
                e,
                exc_info=True,
            )
            return False
    
    def get_config_path(self, config_name: str) -> str:
        """
        Get the path to a configuration file
        
        Args:
            config_name (str): Name of the config (without extension)
            
        Returns:
            str: Path to the configuration file
        """
        return os.path.join(self.config_dir, f"{config_name}.json")
    
    def load_config(self, config_name: str, include_dynamic_controls: bool = False, include_streamdeck_options: bool = False) -> Dict[str, Any]:
        """
        Load configuration from a file

        Args:
            config_name (str): Name of the config (without extension)
            include_dynamic_controls (bool): Whether to include dynamic_controls section
            include_streamdeck_options (bool): Whether to include streamdeck_options section

        Returns:
            Dict[str, Any]: Configuration data
        """
        try:
            raw = self._get_cached_raw_config(config_name)
            config = self._filter_config(
                raw, include_dynamic_controls, include_streamdeck_options
            )
            logger.debug("Successfully loaded config for %s", config_name)
            return config
        except Exception as e:
            logger.error(
                "Error loading config for %s: %s", config_name, e, exc_info=True
            )
            return self._create_default_config(config_name)
    
    def save_config(self, config_name: str, config: Dict[str, Any]) -> bool:
        """
        Save configuration to a file
        
        Args:
            config_name (str): Name of the config (without extension)
            config (Dict[str, Any]): Configuration data to save
            
        Returns:
            bool: True if successful, False otherwise
        """
        config_path = self.get_config_path(config_name)
        
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            self.invalidate_cache(config_name)
            logger.debug("Successfully saved config for %s", config_name)
            self._notify_config_saved(config_name)
            return True
        except Exception as e:
            logger.error(
                "Error saving config for %s: %s", config_name, e, exc_info=True
            )
            return False

    @staticmethod
    def _notify_config_saved(config_name: str) -> None:
        """Invalidate web-engine caches and notify overlay clients."""
        try:
            from . import web_engine

            inst = getattr(web_engine, "web_engine_instance", None)
            if inst is None:
                return
            inst.invalidate_all_template_configs_cache()
            if hasattr(inst, "broadcast_template_config_updated"):
                inst.broadcast_template_config_updated(config_name)
        except Exception as e:
            logger.debug("Config save notify skipped: %s", e)
    
    def _create_default_config(self, config_name: str) -> Dict[str, Any]:
        """
        Create a default configuration
        
        Args:
            config_name (str): Name of the config (without extension)
            
        Returns:
            Dict[str, Any]: Default configuration
        """
        # This is a simple default config structure
        # You can customize this based on your needs
        return {
            "template_name": config_name,
            "elements": [
                {
                    "type": "text",
                    "id": "example_text",
                    "label": "Example Text",
                    "value": "Default value",
                    "description": "This is an example text field"
                },
                {
                    "type": "color",
                    "id": "example_color",
                    "label": "Example Color",
                    "value": "#ffffff",
                    "description": "This is an example color field",
                    "transparent": False
                }
            ]
        }
    
    def get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all configurations
        
        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of config names to their configurations
        """
        config_names = self.get_config_files()
        configs = {}
        
        for config_name in config_names:
            configs[config_name] = self.load_config(config_name)
        
        return configs
    
    def create_config(self, config_name: str, default_config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Create a new configuration file
        
        Args:
            config_name (str): Name of the config (without extension)
            default_config (Dict[str, Any], optional): Default configuration structure
            
        Returns:
            bool: True if successful, False otherwise
        """
        config_path = self.get_config_path(config_name)
        
        try:
            if os.path.exists(config_path):
                logger.warning(f"Config file for {config_name} already exists.")
                return False
            
            # Use provided default config or create a new one
            if default_config is None:
                default_config = self._create_default_config(config_name)
            
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)

            self.invalidate_cache(config_name)
            logger.debug("Successfully created config for %s", config_name)
            self._notify_config_saved(config_name)
            return True
        except Exception as e:
            logger.error(f"Error creating config for {config_name}: {str(e)}", exc_info=True)
            return False
    
    def delete_config(self, config_name: str) -> bool:
        """
        Delete a configuration file

        Args:
            config_name (str): Name of the config (without extension)

        Returns:
            bool: True if successful, False otherwise
        """
        config_path = self.get_config_path(config_name)

        try:
            if os.path.exists(config_path):
                os.remove(config_path)
                self.invalidate_cache(config_name)
                logger.debug("Successfully deleted config for %s", config_name)
                try:
                    from . import web_engine

                    inst = getattr(web_engine, "web_engine_instance", None)
                    if inst is not None:
                        inst.invalidate_all_template_configs_cache()
                except Exception:
                    pass
                return True
            else:
                logger.warning(f"Config file for {config_name} not found.")
                return False
        except Exception as e:
            logger.error(f"Error deleting config for {config_name}: {str(e)}", exc_info=True)
            return False

    def get_streamdeck_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all template configurations that have Stream Deck integration

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary of template names to their Stream Deck configurations
        """
        all_configs = self.get_config_files()
        streamdeck_configs = {}

        for config_name in all_configs:
            try:
                config = self.load_config(config_name, include_streamdeck_options=True)
                if isinstance(config, dict) and 'streamdeck_options' in config:
                    streamdeck_configs[config_name] = config
                    logger.debug(f"Found Stream Deck config for {config_name}")
            except Exception as e:
                logger.warning(f"Error checking Stream Deck config for {config_name}: {str(e)}")

        logger.debug(f"Found {len(streamdeck_configs)} templates with Stream Deck integration")
        return streamdeck_configs


def normalize_template_match_key(s: Any) -> str:
    """Lowercase alnum-only key for matching reward titles to template config names."""
    return "".join(c for c in str(s or "").strip().lower() if c.isalnum())


def _config_element_value(config: Optional[Dict[str, Any]], element_id: str) -> Any:
    elements = config.get("elements") if isinstance(config, dict) else None
    if not isinstance(elements, list):
        return None
    for el in elements:
        if isinstance(el, dict) and el.get("id") == element_id:
            return el.get("value")
    return None


def find_template_config_for_reward_title(
    reward_title: str,
) -> Optional[Dict[str, Any]]:
    """
    Match a Twitch reward title to a template JSON (config file stem or template_name).
    """
    want = normalize_template_match_key(reward_title)
    if not want:
        return None
    parser = TemplateConfigParser()
    for stem in parser.get_config_files():
        try:
            cfg = parser.load_config(stem, include_dynamic_controls=False)
        except Exception as e:
            logger.debug("Skipping template config %s: %s", stem, e)
            continue
        if not isinstance(cfg, dict):
            continue
        if normalize_template_match_key(stem) == want:
            return cfg
        tn = cfg.get("template_name")
        if tn and normalize_template_match_key(tn) == want:
            return cfg
    return None


def point_reward_template_duration_seconds(config: Dict[str, Any]) -> float:
    """Duration field: values >100 treated as milliseconds (boo-style); else seconds."""
    raw = _config_element_value(config, "Duration")
    if raw is None or raw == "":
        return 5.0
    try:
        dur_num = float(raw)
    except (TypeError, ValueError):
        return 5.0
    if dur_num > 100:
        return dur_num / 1000.0
    return dur_num


def match_point_reward_dedicated_template(
    reward_title: str,
) -> Optional[Dict[str, Any]]:
    """
    If a companion template_config exists for this reward title, return queue-related fields.

    Returns:
        None if no JSON match, else {"queued": bool, "duration_seconds": float}.
    """
    cfg = find_template_config_for_reward_title(reward_title)
    if not cfg:
        return None
    queued = bool(_config_element_value(cfg, "Queued"))
    duration_seconds = point_reward_template_duration_seconds(cfg)
    return {"queued": queued, "duration_seconds": duration_seconds} 