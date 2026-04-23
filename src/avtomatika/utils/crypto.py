# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

import base64
import hashlib
from logging import getLogger

from cryptography.fernet import Fernet, InvalidToken

logger = getLogger(__name__)

# Cache Fernet instances to avoid repeated key derivation
# Format: {raw_key: Fernet}
_FERNET_CACHE: dict[str, Fernet] = {}


def get_fernet(key: str) -> Fernet:
    """
    Returns a Fernet instance derived from the provided key.
    Uses SHA-256 to ensure any arbitrary string can be used as a key.
    """
    if key in _FERNET_CACHE:
        return _FERNET_CACHE[key]

    # Derive a valid 32-byte URL-safe base64 key
    digest = hashlib.sha256(key.encode()).digest()
    b64_key = base64.urlsafe_b64encode(digest)
    fernet = Fernet(b64_key)

    # Simple cache cleanup if it grows too large
    if len(_FERNET_CACHE) > 100:
        _FERNET_CACHE.clear()

    _FERNET_CACHE[key] = fernet
    return fernet


def encrypt_token(token: str, key: str) -> str:
    """Encrypts a token using the provided key."""
    if not token:
        return ""
    f = get_fernet(key)
    return str(f.encrypt(token.encode()).decode())


def decrypt_token(cipher_token: str, key: str) -> str | None:
    """
    Decrypts a token using the provided key.
    Returns None if decryption fails (e.g. invalid key).
    """
    if not cipher_token:
        return None
    try:
        f = get_fernet(key)
        return str(f.decrypt(cipher_token.encode()).decode())
    except InvalidToken:
        logger.error("Failed to decrypt worker token. Encryption key might be incorrect.")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during token decryption: {e}")
        return None
