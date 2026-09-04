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
import threading
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Application-specific salt for key derivation (legacy installs only)
APP_SALT = b'MycelianStreamApp2024_Salt_Key'

_KEY_FILENAME = ".encryption_key"
_key_lock = threading.Lock()
_cached_key = None
_cached_fernet = None


class EncryptionError(Exception):
    """Raised when a value cannot be encrypted (fail closed; do not persist plaintext)."""


def _key_file_path() -> str:
    from .path_utils import get_data_path

    return get_data_path(_KEY_FILENAME)


def _derive_legacy_key() -> bytes:
    """Key derived from machine id + app salt (pre-per-install-key installs)."""
    machine_id = os.environ.get('MYCELIAN_MACHINE_ID', 'default_machine')
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=APP_SALT,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))


def _persist_key(key: bytes, path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_key_from_file(path: str) -> bytes:
    with open(path, "rb") as f:
        raw = f.read().strip()
    if not raw:
        raise EncryptionError("Encryption key file is empty")
    return raw


def _get_encryption_key(*, decrypting: bool = False) -> bytes:
    """
    Per-install Fernet key stored in the data dir.

    If the key file is missing:
    - decrypt path (existing ciphertext): persist the legacy derived key
    - encrypt path with no prior ciphertext: random key
    - MYCELIAN_MACHINE_ID set: persist derived key (legacy env-based installs)
    """
    global _cached_key
    with _key_lock:
        if _cached_key is not None:
            return _cached_key
        path = _key_file_path()
        if os.path.isfile(path):
            _cached_key = _load_key_from_file(path)
            return _cached_key

        derived = _derive_legacy_key()
        use_legacy = decrypting or bool(os.environ.get("MYCELIAN_MACHINE_ID"))
        key = derived if use_legacy else Fernet.generate_key()
        try:
            _persist_key(key, path)
        except FileExistsError:
            _cached_key = _load_key_from_file(path)
            return _cached_key
        _cached_key = key
        logger.info(
            "Persisted %s encryption key to data dir",
            "legacy" if use_legacy else "new",
        )
        return _cached_key


def _get_fernet(*, decrypting: bool = False) -> Fernet:
    global _cached_fernet
    key = _get_encryption_key(decrypting=decrypting)
    with _key_lock:
        if _cached_fernet is None:
            _cached_fernet = Fernet(key)
        return _cached_fernet


def encrypt_value(value: str) -> str:
    """
    Encrypt a string value.
    
    Args:
        value (str): The value to encrypt
        
    Returns:
        str: The encrypted value as a base64 string

    Raises:
        EncryptionError: if encryption fails (never returns plaintext to persist)
    """
    try:
        if not value:
            return ""
        
        fernet = _get_fernet(decrypting=False)
        
        # Encrypt the value
        encrypted_bytes = fernet.encrypt(value.encode())
        
        # Return as base64 string for storage
        return base64.urlsafe_b64encode(encrypted_bytes).decode()
        
    except EncryptionError:
        raise
    except Exception as e:
        logger.error(f"Error encrypting value: {str(e)}")
        try:
            from .notification_engine import notify_critical

            notify_critical(
                "Credential encryption failed. Secrets were not saved.",
                dedupe_key="crypto:encrypt_failed",
                dedupe_cooldown_sec=300.0,
            )
        except Exception:
            pass
        raise EncryptionError(str(e)) from e

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
        
        fernet = _get_fernet(decrypting=True)
        
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