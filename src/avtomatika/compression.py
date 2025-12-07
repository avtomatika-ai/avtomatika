from gzip import GzipFile
from io import BytesIO
from typing import Awaitable, Callable

from aiohttp import web
from zstandard import ZstdCompressor

# Define a type for the middleware handler
Handler = Callable[[web.Request], Awaitable[web.Response]]


def _compress_gzip(data: bytes) -> bytes:
    """Compresses data using gzip in a way that works reliably."""
    buf = BytesIO()
    with GzipFile(fileobj=buf, mode="wb", compresslevel=9) as f:
        f.write(data)
    return buf.getvalue()


@web.middleware
async def compression_middleware(
    request: web.Request,
    handler: Handler,
) -> web.Response:
    """AIOHTTP middleware to compress responses using zstd or gzip.
    It prioritizes zstd if the client supports both.
    """
    accept_encoding = request.headers.get("Accept-Encoding", "").lower()

    compress_func = None
    encoding = None

    if "zstd" in accept_encoding:
        compressor = ZstdCompressor()
        compress_func = compressor.compress
        encoding = "zstd"
    elif "gzip" in accept_encoding:
        compress_func = _compress_gzip
        encoding = "gzip"

    response = await handler(request)

    if isinstance(response, web.WebSocketResponse):
        return response

    if (
        not compress_func
        or not encoding
        or "Content-Encoding" in response.headers
        or not isinstance(response.body, bytes)  # Can only compress bytes
    ):
        return response

    if len(response.body) < 500:
        return response

    try:
        compressed_body = compress_func(response.body)

        # Create a new response with the compressed body.
        # This is more reliable than modifying the response in-place,
        # as it avoids issues with internal state of the original response object.
        new_response = web.Response(
            body=compressed_body,
            status=response.status,
            reason=response.reason,
            headers={"Content-Encoding": encoding},
        )
        # Copy over essential headers, but avoid problematic ones like Content-Length
        # which will be recalculated by aiohttp for the new body.
        if response.content_type is not None:
            new_response.content_type = response.content_type
        if response.charset is not None:
            new_response.charset = response.charset

        return new_response

    except Exception:
        # If compression fails, it's safer to return the original uncompressed response.
        return response
