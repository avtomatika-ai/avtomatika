# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import gzip
from unittest.mock import Mock

import pytest
import src.avtomatika.compression
import zstandard
from aiohttp import web
from src.avtomatika.compression import _compress_gzip, compression_middleware


@pytest.mark.asyncio
async def test_no_compression_for_small_body():
    """Ensures responses with a body smaller than 500 bytes are not compressed."""
    request = Mock()
    request.headers = {"Accept-Encoding": "zstd"}

    # The handler returns a response with a small body
    async def handler(req):
        return web.Response(body=b"small body")

    response = await compression_middleware(request, handler)
    assert "Content-Encoding" not in response.headers


@pytest.mark.asyncio
async def test_no_compression_if_already_encoded():
    """Ensures responses that already have a Content-Encoding are not compressed again."""
    request = Mock()
    request.headers = {"Accept-Encoding": "zstd"}

    async def handler(req):
        response = web.Response(body=b"i am already compressed" * 100)
        response.headers["Content-Encoding"] = "br"
        return response

    response = await compression_middleware(request, handler)
    assert response.headers["Content-Encoding"] == "br"


@pytest.mark.asyncio
async def test_websocket_response_is_ignored():
    """Ensures WebSocket responses are not compressed."""
    request = Mock()
    request.headers = {"Accept-Encoding": "zstd"}

    async def handler(req):
        ws_response = web.WebSocketResponse()
        # In a real scenario, prepare() would be called, but for middleware testing,
        # returning the instance is sufficient.
        return ws_response

    response = await compression_middleware(request, handler)
    assert isinstance(response, web.WebSocketResponse)
    assert "Content-Encoding" not in response.headers


@pytest.mark.asyncio
async def test_zstd_compression_occurs():
    """Tests that zstd compression is applied correctly."""
    request = Mock()
    request.headers = {"Accept-Encoding": "zstd"}
    large_body = b"some large body content" * 100

    async def handler(req):
        return web.Response(body=large_body)

    response = await compression_middleware(request, handler)
    assert response.headers["Content-Encoding"] == "zstd"

    decompressor = zstandard.ZstdDecompressor()
    decompressed_body = decompressor.decompress(response.body)
    assert decompressed_body == large_body


@pytest.mark.asyncio
async def test_gzip_compression_occurs():
    """Tests that gzip compression is applied correctly."""
    request = Mock()
    request.headers = {"Accept-Encoding": "gzip"}
    large_body = b"some large body content for gzip" * 100

    async def handler(req):
        return web.Response(body=large_body)

    response = await compression_middleware(request, handler)
    assert response.headers["Content-Encoding"] == "gzip"

    decompressed_body = gzip.decompress(response.body)
    assert decompressed_body == large_body


@pytest.mark.asyncio
async def test_compression_failure_returns_original_response():
    """Tests that if the compression function fails, the original response is returned."""
    request = Mock()
    request.headers = {"Accept-Encoding": "gzip"}
    large_body = b"some large body that will fail to compress" * 100

    # Mock the compression function to raise an exception
    original_compress = _compress_gzip
    try:

        def failing_compress_gzip(data):
            raise ValueError("Compression failed!")

        # Monkeypatch the function
        src.avtomatika.compression._compress_gzip = failing_compress_gzip

        async def handler(req):
            return web.Response(body=large_body)

        response = await compression_middleware(request, handler)
        assert "Content-Encoding" not in response.headers
        assert response.body == large_body
    finally:
        # Restore the original function
        src.avtomatika.compression._compress_gzip = original_compress
