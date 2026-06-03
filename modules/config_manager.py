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

"""
Configuration Manager for Mycelian

This module handles application configuration that needs to be available
before database initialization, particularly the database type selection.

The configuration is stored in a JSON file separate from any database.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict

from .path_utils import get_data_path
from .database_manager import normalize_firebase_database_url
from .notification_engine import nav_actions_settings, notify_critical

logger = logging.getLogger(__name__)

@dataclass
class AppConfig:
    """Application configuration structure"""
    # Database configuration - read before database init
    database_type: str = "sql"  # "sql", "firebase", "mongodb"
    
    # Database connection parameters
    sql_database_path: str = "mycelian.db"
    firebase_service_account_path: str = "ServiceAccountKey.json"
    firebase_database_url: str = "https://your-project-default-rtdb.firebaseio.com/"
    mongodb_connection_string: str = "mongodb://localhost:27017/"
    mongodb_database_name: str = "mycelian"
    
    # Common database settings
    connection_timeout: int = 30
    retry_attempts: int = 3
    
    # Application metadata
    config_version: str = "1.0"
    last_updated: str = ""

class ConfigManager:
    """Manages application configuration stored in external files"""
    
    def __init__(self, config_path: str = "config.json"):
        # Use path utils to get correct config path for exe
        if config_path == "config.json":
            self.config_path = Path(get_data_path(config_path))
        else:
            self.config_path = Path(config_path)
        self._config: Optional[AppConfig] = None
        self._initialized = False
    
    def initialize(self) -> bool:
        """Initialize the configuration manager"""
        try:
            if self._initialized:
                return True
            
            logger.info("Initializing configuration manager...")
            
            # Try to load existing config
            if self.config_path.exists():
                logger.info(f"Loading existing config from {self.config_path}")
                self._config = self._load_config()
            else:
                logger.info("No existing config found, creating default config")
                self._config = self._create_default_config()
                self._save_config()
            
            self._initialized = True
            logger.info(f"Configuration initialized. Database type: {self._config.database_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize configuration manager: {str(e)}", exc_info=True)
            notify_critical(
                "Failed to initialize application configuration. Check config.json and logs.",
                dedupe_key="config:init_failed",
                actions=nav_actions_settings("App Settings"),
            )
            return False
    
    def _load_config(self) -> AppConfig:
        """Load configuration from file"""
        try:
            with open(self.config_path, 'r') as f:
                config_data = json.load(f)
            
            # Validate and create AppConfig object
            # Filter out any unknown fields to handle config upgrades
            valid_fields = {field.name for field in AppConfig.__dataclass_fields__.values()}
            filtered_data = {k: v for k, v in config_data.items() if k in valid_fields}
            
            config = AppConfig(**filtered_data)
            logger.debug(f"Loaded config: database_type={config.database_type}")
            return config
            
        except Exception as e:
            logger.error(f"Error loading config from {self.config_path}: {str(e)}")
            logger.info("Creating default config due to load failure")
            return self._create_default_config()
    
    def _create_default_config(self) -> AppConfig:
        """Create default configuration"""
        from datetime import datetime
        
        config = AppConfig()
        config.last_updated = datetime.now().isoformat()
        logger.info("Created default configuration")
        return config
    
    def _save_config(self) -> bool:
        """Save configuration to file"""
        try:
            if not self._config:
                logger.error("No config to save")
                return False
            
            # Update timestamp
            from datetime import datetime
            self._config.last_updated = datetime.now().isoformat()
            
            # Create directory if it doesn't exist
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save to file with pretty formatting
            config_dict = asdict(self._config)
            with open(self.config_path, 'w') as f:
                json.dump(config_dict, f, indent=2, sort_keys=True)
            
            logger.debug(f"Saved config to {self.config_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving config to {self.config_path}: {str(e)}", exc_info=True)
            notify_critical(
                "Could not save application configuration to disk.",
                dedupe_key="config:save_failed",
                actions=nav_actions_settings("App Settings"),
            )
            return False
    
    def get_config(self) -> Optional[AppConfig]:
        """Get the current configuration"""
        if not self._initialized:
            self.initialize()
        return self._config
    
    def get_database_type(self) -> str:
        """Get the configured database type"""
        if not self._initialized:
            self.initialize()
        return self._config.database_type if self._config else "sql"
    
    def set_database_type(self, database_type: str) -> bool:
        """Set the database type and save configuration"""
        if not self._initialized:
            self.initialize()
        
        if not self._config:
            logger.error("No config available to update")
            return False
        
        if database_type not in ["sql", "firebase", "mongodb"]:
            logger.error(f"Invalid database type: {database_type}")
            return False
        
        old_type = self._config.database_type
        self._config.database_type = database_type
        
        if self._save_config():
            logger.info(f"Database type changed from {old_type} to {database_type}")
            return True
        else:
            # Revert on save failure
            self._config.database_type = old_type
            logger.error(f"Failed to save database type change to {database_type}")
            return False
    
    def update_database_config(self, **kwargs) -> bool:
        """Update database configuration parameters"""
        if not self._initialized:
            self.initialize()
        
        if not self._config:
            logger.error("No config available to update")
            return False
        
        # Update fields that exist in the config
        updated_fields = []
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                if key == "firebase_database_url" and isinstance(value, str):
                    value = normalize_firebase_database_url(value)
                old_value = getattr(self._config, key)
                setattr(self._config, key, value)
                updated_fields.append(f"{key}: {old_value} -> {value}")
        
        if updated_fields:
            logger.info(f"Updated config fields: {'; '.join(updated_fields)}")
            return self._save_config()
        else:
            logger.debug("No valid config fields to update")
            return True
    
    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration as a dictionary"""
        if not self._initialized:
            self.initialize()
        
        if not self._config:
            return {}
        
        return {
            'database_type': self._config.database_type,
            'sql_database_path': self._config.sql_database_path,
            'firebase_service_account_path': self._config.firebase_service_account_path,
            'firebase_database_url': self._config.firebase_database_url,
            'mongodb_connection_string': self._config.mongodb_connection_string,
            'mongodb_database_name': self._config.mongodb_database_name,
            'connection_timeout': self._config.connection_timeout,
            'retry_attempts': self._config.retry_attempts
        }
    
    def export_config(self, export_path: str) -> bool:
        """Export configuration to a different file"""
        try:
            if not self._config:
                logger.error("No config to export")
                return False
            
            export_path_obj = Path(export_path)
            export_path_obj.parent.mkdir(parents=True, exist_ok=True)
            
            config_dict = asdict(self._config)
            with open(export_path_obj, 'w') as f:
                json.dump(config_dict, f, indent=2, sort_keys=True)
            
            logger.info(f"Exported config to {export_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error exporting config to {export_path}: {str(e)}", exc_info=True)
            return False
    
    def import_config(self, import_path: str) -> bool:
        """Import configuration from a different file"""
        try:
            import_path_obj = Path(import_path)
            if not import_path_obj.exists():
                logger.error(f"Import file not found: {import_path}")
                return False
            
            with open(import_path_obj, 'r') as f:
                config_data = json.load(f)
            
            # Validate and create AppConfig object
            valid_fields = {field.name for field in AppConfig.__dataclass_fields__.values()}
            filtered_data = {k: v for k, v in config_data.items() if k in valid_fields}
            
            imported_config = AppConfig(**filtered_data)
            
            # Update current config
            self._config = imported_config
            
            if self._save_config():
                logger.info(f"Imported config from {import_path}")
                return True
            else:
                logger.error(f"Failed to save imported config")
                return False
            
        except Exception as e:
            logger.error(f"Error importing config from {import_path}: {str(e)}", exc_info=True)
            return False

# Global configuration manager instance
config_manager = ConfigManager()

# Convenience functions
def get_database_type() -> str:
    """Get the configured database type"""
    return config_manager.get_database_type()

def set_database_type(database_type: str) -> bool:
    """Set the database type"""
    return config_manager.set_database_type(database_type)

def get_database_config() -> Dict[str, Any]:
    """Get database configuration"""
    return config_manager.get_database_config()

def initialize_config() -> bool:
    """Initialize the configuration manager"""
    return config_manager.initialize() 