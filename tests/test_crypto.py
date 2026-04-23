# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from cryptography.fernet import Fernet

from avtomatika.utils.crypto import decrypt_token, encrypt_token, get_fernet


def test_fernet_derivation():
    key = "any-secret-string"
    f1 = get_fernet(key)
    f2 = get_fernet(key)

    assert isinstance(f1, Fernet)
    assert f1 is f2  # Check caching

    # Different key should result in different Fernet instance
    f3 = get_fernet("other-key")
    assert f1 is not f3


def test_encryption_decryption():
    key = "master-key"
    token = "secret-worker-token"

    cipher = encrypt_token(token, key)
    assert cipher != token
    assert isinstance(cipher, str)

    decrypted = decrypt_token(cipher, key)
    assert decrypted == token


def test_decryption_with_wrong_key():
    key1 = "key-one"
    key2 = "key-two"
    token = "sensitive-data"

    cipher = encrypt_token(token, key1)

    # Decryption with wrong key should return None and log error
    decrypted = decrypt_token(cipher, key2)
    assert decrypted is None


def test_empty_token_handling():
    key = "some-key"
    assert encrypt_token("", key) == ""
    assert decrypt_token("", key) is None


def test_invalid_cipher_handling():
    key = "some-key"
    assert decrypt_token("not-a-fernet-token", key) is None
