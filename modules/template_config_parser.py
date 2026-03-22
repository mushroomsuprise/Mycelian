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

import copy
import glob
import json
import logging
import os
from typing import Any, Dict, List, Optional

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
    
    def get_config_files(self) -> List[str]:
        """
        Get a list of all configuration files
        
        Returns:
            List[str]: List of config filenames without extension
        """
        if not os.path.exists(self.config_dir):
            logger.warning(f"Config directory {self.config_dir} not found.")
            return []
        
        json_files = glob.glob(os.path.join(self.config_dir, '*.json'))
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
            config = self.load_config(config_name, include_dynamic_controls=True)
            
            # Check for "hidden" property at the root level
            if isinstance(config, dict) and config.get('hidden', False):
                logger.debug(f"Config {config_name} is marked as hidden")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error checking if config {config_name} is hidden: {str(e)}", exc_info=True)
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
        config_path = self.get_config_path(config_name)

        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    config = json.load(f)

                # Filter out dynamic_controls if not requested
                if not include_dynamic_controls and isinstance(config, dict) and 'dynamic_controls' in config:
                    config = config.copy()  # Don't modify the original
                    del config['dynamic_controls']

                # Filter out streamdeck_options if not requested
                if not include_streamdeck_options and isinstance(config, dict) and 'streamdeck_options' in config:
                    config = config.copy()  # Don't modify the original
                    del config['streamdeck_options']

                logger.debug(f"Successfully loaded config for {config_name}")
                return config
            else:
                logger.warning(f"Config file for {config_name} not found. Creating default config.")
                default_config = self._create_default_config(config_name)
                self.save_config(config_name, default_config)
                return default_config
        except Exception as e:
            logger.error(f"Error loading config for {config_name}: {str(e)}", exc_info=True)
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
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=4)
            logger.debug(f"Successfully saved config for {config_name}")
            return True
        except Exception as e:
            logger.error(f"Error saving config for {config_name}: {str(e)}", exc_info=True)
            return False
    
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
            
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=4)
            
            logger.debug(f"Successfully created config for {config_name}")
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
                logger.debug(f"Successfully deleted config for {config_name}")
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