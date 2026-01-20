from asyncio import Lock
from logging import getLogger
from typing import Any

from aiohttp import web

logger = getLogger(__name__)


class WebSocketManager:
    """Manages active WebSocket connections from workers."""

    def __init__(self) -> None:
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._lock = Lock()

    async def register(self, worker_id: str, ws: web.WebSocketResponse) -> None:
        """Registers a new WebSocket connection for a worker."""
        async with self._lock:
            if worker_id in self._connections:
                # Close the old connection if it exists
                await self._connections[worker_id].close(code=1008, message=b"New connection established")
            self._connections[worker_id] = ws
            logger.info(f"WebSocket connection registered for worker {worker_id}.")

    async def unregister(self, worker_id: str) -> None:
        """Unregisters a WebSocket connection."""
        async with self._lock:
            if worker_id in self._connections:
                del self._connections[worker_id]
                logger.info(f"WebSocket connection for worker {worker_id} unregistered.")

    async def send_command(self, worker_id: str, command: dict[str, Any]) -> bool:
        """Sends a JSON command to a specific worker."""
        async with self._lock:
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

    @staticmethod
    async def handle_message(worker_id: str, message: dict[str, Any]) -> None:
        """Handles an incoming message from a worker."""
        event_type = message.get("event")
        if event_type == "progress_update":
            # In a real application, you'd likely forward this to a history store
            # or a pub/sub system for real-time UI updates.
            logger.info(
                f"Received progress update from worker {worker_id} for job {message.get('job_id')}: "
                f"{message.get('progress', 0) * 100:.0f}% - {message.get('message', '')}"
            )
        else:
            logger.debug(f"Received unhandled event from worker {worker_id}: {event_type}")

    async def close_all(self) -> None:
        """Closes all active WebSocket connections."""
        async with self._lock:
            logger.info(f"Closing {len(self._connections)} active WebSocket connections...")
            for ws in self._connections.values():
                await ws.close(code=1001, message=b"Server shutdown")
            self._connections.clear()
            logger.info("All WebSocket connections closed.")
