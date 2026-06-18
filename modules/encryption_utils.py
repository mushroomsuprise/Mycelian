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

import base64
import logging
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Application-specific salt for key derivation
# This should be kept secret and consistent across the application
APP_SALT = b'MycelianStreamApp2024_Salt_Key'

def _get_encryption_key() -> bytes:
    """
    Generate a consistent encryption key based on application salt.
    
    Returns:
        bytes: The encryption key for Fernet
    """
    # Use a combination of the app salt and a machine-specific identifier
    # This ensures the key is consistent for the same installation
    machine_id = os.environ.get('MYCELIAN_MACHINE_ID', 'default_machine')
    
    # Derive key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=APP_SALT,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
    return key

def encrypt_value(value: str) -> str:
    """
    Encrypt a string value.
    
    Args:
        value (str): The value to encrypt
        
    Returns:
        str: The encrypted value as a base64 string
    """
    try:
        if not value:
            return ""
        
        key = _get_encryption_key()
        fernet = Fernet(key)
        
        # Encrypt the value
        encrypted_bytes = fernet.encrypt(value.encode())
        
        # Return as base64 string for storage
        return base64.urlsafe_b64encode(encrypted_bytes).decode()
        
    except Exception as e:
        logger.error(f"Error encrypting value: {str(e)}")
        try:
            from .notification_engine import notify_critical

            notify_critical(
                "Credential encryption failed. Secrets may be stored in plain form; check logs.",
                dedupe_key="crypto:encrypt_failed",
                dedupe_cooldown_sec=300.0,
            )
        except Exception:
            pass
        return value  # Return original value if encryption fails

def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypt an encrypted string value.
    
    Args:
        encrypted_value (str): The encrypted value as a base64 string
        
    Returns:
        str: The decrypted value
    """
    try:
        if not encrypted_value:
            return ""
        
        key = _get_encryption_key()
        fernet = Fernet(key)
        
        # Decode from base64
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_value.encode())
        
        # Decrypt the value
        decrypted_bytes = fernet.decrypt(encrypted_bytes)
        
        return decrypted_bytes.decode()
        
    except Exception as e:
        logger.error(f"Error decrypting value: {str(e)}")
        try:
            from .notification_engine import nav_actions_settings, notify_critical

            notify_critical(
                "Could not decrypt stored credentials. You may need to re-enter keys in Settings.",
                dedupe_key="crypto:decrypt_failed",
                dedupe_cooldown_sec=300.0,
                actions=nav_actions_settings("App Settings"),
            )
        except Exception:
            pass
        return encrypted_value  # Return original value if decryption fails

def is_encrypted(value: str) -> bool:
    """
    Check if a value appears to be encrypted.
    
    Args:
        value (str): The value to check
        
    Returns:
        bool: True if the value appears to be encrypted
    """
    try:
        if not value:
            return False
        
        # Try to decode as base64 and check if it looks like encrypted data
        decoded = base64.urlsafe_b64decode(value.encode())
        
        # Encrypted values should be at least a certain length
        # and have specific characteristics
        return len(decoded) >= 32 and len(value) > 50
        
    except Exception:
        return False

def ensure_encrypted(value: str) -> str:
    """
    Ensure a value is encrypted. If it's already encrypted, return as-is.
    If not encrypted, encrypt it.
    
    Args:
        value (str): The value to ensure is encrypted
        
    Returns:
        str: The encrypted value
    """
    if is_encrypted(value):
        return value
    else:
        return encrypt_value(value)

def ensure_decrypted(value: str) -> str:
    """
    Ensure a value is decrypted. If it's encrypted, decrypt it.
    If not encrypted, return as-is.
    
    Args:
        value (str): The value to ensure is decrypted
        
    Returns:
        str: The decrypted value
    """
    if is_encrypted(value):
        return decrypt_value(value)
    else:
        return value

logger.info("Encryption utilities initialized") 