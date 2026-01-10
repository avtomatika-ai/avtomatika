from typing import Any


class AsyncDictStore:
    """A simple asynchronous in-memory key-value store.
    Simulates the behavior of a persistent store for use in blueprints.
    """

    def __init__(self, initial_data: dict[str, Any]):
        self._data = initial_data.copy()

    async def get(self, key: str) -> Any:
        """Asynchronously gets a value by key."""
        return self._data.get(key)

    async def set(self, key: str, value: Any) -> None:
        """Asynchronously sets a value by key."""
        self._data[key] = value
