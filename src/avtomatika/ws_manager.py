# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from asyncio import gather
from logging import getLogger
from typing import Any

from aiohttp import web

from .storage.base import StorageBackend

logger = getLogger(__name__)


class WebSocketManager:
    """Manages active WebSocket connections from workers."""

    def __init__(self, storage: StorageBackend) -> None:
        self._connections: dict[str, web.WebSocketResponse] = {}
        self.storage = storage

    async def register(self, worker_id: str, ws: web.WebSocketResponse) -> None:
        """Registers a new WebSocket connection for a worker."""
        if worker_id in self._connections:
            # Close the old connection if it exists
            await self._connections[worker_id].close(code=1008, message=b"New connection established")
        self._connections[worker_id] = ws
        logger.info(f"WebSocket connection registered for worker {worker_id}.")

    async def unregister(self, worker_id: str) -> None:
        """Unregisters a WebSocket connection."""
        if worker_id in self._connections:
            del self._connections[worker_id]
            logger.info(f"WebSocket connection for worker {worker_id} unregistered.")

    async def send_command(self, worker_id: str, command: dict[str, Any]) -> bool:
        """Sends a JSON command to a specific worker."""
        connection = self._connections.get(worker_id)
        if connection and not connection.closed:
            try:
                await connection.send_json(command)
                logger.info(f"Sent command {command['command']} to worker {worker_id}.")
                return True
            except Exception as e:
                logger.error(f"Failed to send command to worker {worker_id}: {e}")
                return False
        else:
            logger.warning(f"Cannot send command: No active WebSocket connection for worker {worker_id}.")
            return False

    async def handle_message(self, worker_id: str, message: dict[str, Any]) -> None:
        """Handles an incoming message from a worker.
        Now just logs the event as most logic has moved to generic events.
        """
        event_type = message.get("event_type")
        logger.debug(f"Received WebSocket message from worker {worker_id}: {event_type}")

    async def close_all(self) -> None:
        """Closes all active WebSocket connections."""

        logger.info(f"Closing {len(self._connections)} active WebSocket connections...")
        tasks = [ws.close(code=1001, message=b"Server shutdown") for ws in self._connections.values()]
        if tasks:
            await gather(*tasks, return_exceptions=True)
        self._connections.clear()
        logger.info("All WebSocket connections closed.")
