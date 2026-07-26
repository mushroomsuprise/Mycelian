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

"""
API Credentials Manager for Mycelian

This module handles the storage and management of default API credentials
for various services like Twitch and Spotify.

The credentials are stored encrypted in a separate JSON configuration file.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict

from .encryption_utils import (
    encrypt_value,
    decrypt_value,
    ensure_encrypted,
    ensure_decrypted,
)
from .notification_engine import nav_actions_settings, notify_critical

logger = logging.getLogger(__name__)


@dataclass
class APICredentials:
    """API credentials structure"""

    # Twitch API credentials
    twitch_client_id: str = ""
    twitch_client_secret: str = ""

    # Twitch Chatbot API credentials
    chatbot_client_id: str = ""
    chatbot_client_secret: str = ""

    # Spotify API credentials
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # YouTube OAuth credentials (live chat / memberships / Super Chats)
    youtube_client_id: str = ""
    youtube_client_secret: str = ""

    # Configuration metadata
    config_version: str = "1.0"
    last_updated: str = ""


class APICredentialsManager:
    """Manages API credentials stored in encrypted configuration files"""

    def __init__(self, config_path: str = "api_credentials.json"):
        self.config_path = Path(config_path)
        self._credentials: Optional[APICredentials] = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize the API credentials manager"""
        try:
            if self._initialized:
                return True

            logger.info("Initializing API credentials manager...")

            # Try to load existing config
            if self.config_path.exists():
                logger.info(f"Loading existing credentials from {self.config_path}")
                self._credentials = self._load_credentials()
            else:
                logger.info("No existing credentials found, creating empty credentials")
                self._credentials = self._create_empty_credentials()
                self._save_credentials()

            self._initialized = True
            logger.info("API credentials initialized successfully")
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize API credentials manager: {str(e)}", exc_info=True
            )
            notify_critical(
                "Failed to initialize API credentials. Check api_credentials.json and permissions.",
                dedupe_key="api_creds:init_failed",
                actions=nav_actions_settings("App Settings"),
            )
            return False

    def _load_credentials(self) -> APICredentials:
        """Load credentials from file"""
        try:
            with open(self.config_path, "r") as f:
                credentials_data = json.load(f)

            # Debug logging
            logger.debug(
                f"Raw JSON loaded, chatbot_client_id length: {len(credentials_data.get('chatbot_client_id', ''))}"
            )
            logger.debug(
                f"Raw JSON loaded, chatbot_client_secret length: {len(credentials_data.get('chatbot_client_secret', ''))}"
            )

            # Validate and create APICredentials object
            # Filter out any unknown fields to handle config upgrades
            valid_fields = {
                field.name for field in APICredentials.__dataclass_fields__.values()
            }
            filtered_data = {
                k: v for k, v in credentials_data.items() if k in valid_fields
            }

            # Debug logging after filtering
            logger.debug(
                f"After filtering, chatbot_client_id length: {len(filtered_data.get('chatbot_client_id', ''))}"
            )
            logger.debug(
                f"After filtering, chatbot_client_secret length: {len(filtered_data.get('chatbot_client_secret', ''))}"
            )

            credentials = APICredentials(**filtered_data)

            # Debug logging after dataclass creation
            logger.debug(
                f"After dataclass creation, chatbot_client_id length: {len(credentials.chatbot_client_id) if credentials.chatbot_client_id else 0}"
            )
            logger.debug(
                f"After dataclass creation, chatbot_client_secret length: {len(credentials.chatbot_client_secret) if credentials.chatbot_client_secret else 0}"
            )

            logger.debug("Loaded API credentials from file")
            return credentials

        except Exception as e:
            logger.error(f"Error loading credentials from {self.config_path}: {str(e)}")
            logger.info("Creating empty credentials due to load failure")
            return self._create_empty_credentials()

    def _create_empty_credentials(self) -> APICredentials:
        """Create empty API credentials"""
        from datetime import datetime

        credentials = APICredentials()
        credentials.last_updated = datetime.now().isoformat()

        logger.info("Created empty API credentials")
        return credentials

    def _save_credentials(self) -> bool:
        """Save credentials to file"""
        try:
            if not self._credentials:
                logger.error("No credentials to save")
                return False

            # Update timestamp
            from datetime import datetime

            self._credentials.last_updated = datetime.now().isoformat()

            # Create directory if it doesn't exist
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Save to file with pretty formatting
            credentials_dict = asdict(self._credentials)
            with open(self.config_path, "w") as f:
                json.dump(credentials_dict, f, indent=2, sort_keys=True)

            logger.debug(f"Saved credentials to {self.config_path}")
            return True

        except Exception as e:
            logger.error(
                f"Error saving credentials to {self.config_path}: {str(e)}",
                exc_info=True,
            )
            notify_critical(
                "Could not save API credentials to disk.",
                dedupe_key="api_creds:save_failed",
                actions=nav_actions_settings("App Settings"),
            )
            return False

    def get_credentials(self) -> Optional[APICredentials]:
        """Get the current API credentials"""
        if not self._initialized:
            self.initialize()
        return self._credentials

    def get_twitch_credentials(self) -> Dict[str, str]:
        """Get Twitch API credentials (decrypted)"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            return {"client_id": "", "client_secret": ""}

        return {
            "client_id": ensure_decrypted(self._credentials.twitch_client_id),
            "client_secret": ensure_decrypted(self._credentials.twitch_client_secret),
        }

    def get_chatbot_credentials(self) -> Dict[str, str]:
        """Get Twitch Chatbot API credentials (decrypted)"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            return {"client_id": "", "client_secret": ""}

        # Debug logging
        raw_client_id = self._credentials.chatbot_client_id
        raw_client_secret = self._credentials.chatbot_client_secret
        logger.debug(
            f"Raw chatbot_client_id length: {len(raw_client_id) if raw_client_id else 0}"
        )
        logger.debug(
            f"Raw chatbot_client_secret length: {len(raw_client_secret) if raw_client_secret else 0}"
        )

        decrypted_client_id = ensure_decrypted(raw_client_id)
        decrypted_client_secret = ensure_decrypted(raw_client_secret)

        logger.debug(
            f"Decrypted chatbot_client_id length: {len(decrypted_client_id) if decrypted_client_id else 0}"
        )
        logger.debug(
            f"Decrypted chatbot_client_secret length: {len(decrypted_client_secret) if decrypted_client_secret else 0}"
        )

        return {
            "client_id": decrypted_client_id,
            "client_secret": decrypted_client_secret,
        }

    def get_spotify_credentials(self) -> Dict[str, str]:
        """Get Spotify API credentials (decrypted)"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            return {"client_id": "", "client_secret": ""}

        return {
            "client_id": ensure_decrypted(self._credentials.spotify_client_id),
            "client_secret": ensure_decrypted(self._credentials.spotify_client_secret),
        }

    def update_twitch_credentials(
        self, client_id: str = None, client_secret: str = None
    ) -> bool:
        """Update Twitch API credentials"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            logger.error("No credentials available to update")
            return False

        updated = False
        if client_id is not None:
            self._credentials.twitch_client_id = ensure_encrypted(client_id)
            updated = True

        if client_secret is not None:
            self._credentials.twitch_client_secret = ensure_encrypted(client_secret)
            updated = True

        if updated:
            logger.info("Updated Twitch credentials")
            return self._save_credentials()

        return True

    def update_chatbot_credentials(
        self, client_id: str = None, client_secret: str = None
    ) -> bool:
        """Update Twitch Chatbot API credentials"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            logger.error("No credentials available to update")
            return False

        updated = False
        if client_id is not None:
            self._credentials.chatbot_client_id = ensure_encrypted(client_id)
            updated = True

        if client_secret is not None:
            self._credentials.chatbot_client_secret = ensure_encrypted(client_secret)
            updated = True

        if updated:
            logger.info("Updated Chatbot credentials")
            return self._save_credentials()

        return True

    def update_spotify_credentials(
        self, client_id: str = None, client_secret: str = None
    ) -> bool:
        """Update Spotify API credentials"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            logger.error("No credentials available to update")
            return False

        updated = False
        if client_id is not None:
            self._credentials.spotify_client_id = ensure_encrypted(client_id)
            updated = True

        if client_secret is not None:
            self._credentials.spotify_client_secret = ensure_encrypted(client_secret)
            updated = True

        if updated:
            logger.info("Updated Spotify credentials")
            return self._save_credentials()

        return True

    def get_youtube_credentials(self) -> Dict[str, str]:
        """Get YouTube OAuth client credentials (decrypted)"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            return {"client_id": "", "client_secret": ""}

        return {
            "client_id": ensure_decrypted(self._credentials.youtube_client_id),
            "client_secret": ensure_decrypted(self._credentials.youtube_client_secret),
        }

    def update_youtube_credentials(
        self, client_id: str = None, client_secret: str = None
    ) -> bool:
        """Update YouTube OAuth client credentials"""
        if not self._initialized:
            self.initialize()

        if not self._credentials:
            logger.error("No credentials available to update")
            return False

        updated = False
        if client_id is not None:
            self._credentials.youtube_client_id = ensure_encrypted(client_id)
            updated = True

        if client_secret is not None:
            self._credentials.youtube_client_secret = ensure_encrypted(client_secret)
            updated = True

        if updated:
            logger.info("Updated YouTube OAuth credentials")
            return self._save_credentials()

        return True

    def export_credentials(self, export_path: str) -> bool:
        """Export credentials to a different file"""
        try:
            if not self._credentials:
                logger.error("No credentials to export")
                return False

            export_path_obj = Path(export_path)
            export_path_obj.parent.mkdir(parents=True, exist_ok=True)

            credentials_dict = asdict(self._credentials)
            with open(export_path_obj, "w") as f:
                json.dump(credentials_dict, f, indent=2, sort_keys=True)

            logger.info(f"Exported credentials to {export_path}")
            return True

        except Exception as e:
            logger.error(
                f"Error exporting credentials to {export_path}: {str(e)}", exc_info=True
            )
            return False

    def import_credentials(self, import_path: str) -> bool:
        """Import credentials from a different file"""
        try:
            import_path_obj = Path(import_path)
            if not import_path_obj.exists():
                logger.error(f"Import file not found: {import_path}")
                return False

            with open(import_path_obj, "r") as f:
                credentials_data = json.load(f)

            # Validate and create APICredentials object
            valid_fields = {
                field.name for field in APICredentials.__dataclass_fields__.values()
            }
            filtered_data = {
                k: v for k, v in credentials_data.items() if k in valid_fields
            }

            imported_credentials = APICredentials(**filtered_data)

            # Update current credentials
            self._credentials = imported_credentials

            if self._save_credentials():
                logger.info(f"Imported credentials from {import_path}")
                return True
            else:
                logger.error(f"Failed to save imported credentials")
                return False

        except Exception as e:
            logger.error(
                f"Error importing credentials from {import_path}: {str(e)}",
                exc_info=True,
            )
            return False


# Global API credentials manager instance
api_credentials_manager = APICredentialsManager()


# Convenience functions for backward compatibility
def get_twitch_credentials() -> Dict[str, str]:
    """Get Twitch API credentials"""
    return api_credentials_manager.get_twitch_credentials()


def update_twitch_credentials(client_id: str = None, client_secret: str = None) -> bool:
    """Update Twitch API credentials"""
    return api_credentials_manager.update_twitch_credentials(client_id=client_id, client_secret=client_secret)


def get_chatbot_credentials() -> Dict[str, str]:
    """Get Twitch Chatbot API credentials"""
    return api_credentials_manager.get_chatbot_credentials()


def get_spotify_credentials() -> Dict[str, str]:
    """Get Spotify API credentials"""
    return api_credentials_manager.get_spotify_credentials()


def get_youtube_credentials() -> Dict[str, str]:
    """Get YouTube OAuth client credentials"""
    return api_credentials_manager.get_youtube_credentials()


def update_youtube_credentials(client_id: str = None, client_secret: str = None) -> bool:
    """Update YouTube OAuth client credentials"""
    return api_credentials_manager.update_youtube_credentials(
        client_id=client_id, client_secret=client_secret
    )


def initialize_api_credentials() -> bool:
    """Initialize the API credentials manager"""
    return api_credentials_manager.initialize()


# For backward compatibility with the old global variables
def get_encrypted_twitch_client_id() -> str:
    """Get encrypted Twitch client ID"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.twitch_client_id
    return ""


def get_encrypted_twitch_client_secret() -> str:
    """Get encrypted Twitch client secret"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.twitch_client_secret
    return ""


def get_encrypted_spotify_client_id() -> str:
    """Get encrypted Spotify client ID"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.spotify_client_id
    return ""


def get_encrypted_spotify_client_secret() -> str:
    """Get encrypted Spotify client secret"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.spotify_client_secret
    return ""


def get_encrypted_youtube_client_id() -> str:
    """Get encrypted YouTube OAuth client ID"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.youtube_client_id
    return ""


def get_encrypted_youtube_client_secret() -> str:
    """Get encrypted YouTube OAuth client secret"""
    if not api_credentials_manager._initialized:
        api_credentials_manager.initialize()
    if api_credentials_manager._credentials:
        return api_credentials_manager._credentials.youtube_client_secret
    return ""

