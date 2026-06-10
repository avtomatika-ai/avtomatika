# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from base64 import urlsafe_b64encode
from hashlib import sha256
from logging import getLogger

from cryptography.fernet import Fernet, InvalidToken

logger = getLogger(__name__)


def get_fernet(key: str, cache: dict[str, Fernet] | None = None) -> Fernet:
    """
    Returns a Fernet instance derived from the provided key.
    Uses SHA-256 to ensure any arbitrary string can be used as a key.
    """
    if cache is not None and key in cache:
        return cache[key]

    # Derive a valid 32-byte URL-safe base64 key
    digest = sha256(key.encode()).digest()
    b64_key = urlsafe_b64encode(digest)
    fernet = Fernet(b64_key)

    if cache is not None:
        if len(cache) > 100:
            cache.clear()
        cache[key] = fernet

    return fernet


def encrypt_token(token: str, key: str, cache: dict[str, Fernet] | None = None) -> str:
    """Encrypts a token using the provided key."""
    if not token:
        return ""
    f = get_fernet(key, cache)
    return str(f.encrypt(token.encode()).decode())


def decrypt_token(cipher_token: str, key: str, cache: dict[str, Fernet] | None = None) -> str | None:
    """
    Decrypts a token using the provided key.
    Returns None if decryption fails (e.g. invalid key).
    """
    if not cipher_token:
        return None
    try:
        f = get_fernet(key, cache)
        return str(f.decrypt(cipher_token.encode()).decode())
    except InvalidToken:
        logger.error("Failed to decrypt worker token. Encryption key might be incorrect.")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during token decryption: {e}")
        return None
