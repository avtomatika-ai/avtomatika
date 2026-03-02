# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


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
