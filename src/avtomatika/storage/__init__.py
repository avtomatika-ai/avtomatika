from contextlib import suppress

from .base import StorageBackend
from .memory import MemoryStorage

__all__ = ["StorageBackend", "MemoryStorage"]

with suppress(ImportError):
    from .redis import RedisStorage  # noqa: F401

    __all__.append("RedisStorage")
