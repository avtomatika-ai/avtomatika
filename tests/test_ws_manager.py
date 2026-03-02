# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from logging import getLogger
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from avtomatika.ws_manager import WebSocketManager

logger = getLogger(__name__)


@pytest.fixture
def storage():
    storage_mock = AsyncMock()
    storage_mock.update_job_state = AsyncMock()
    return storage_mock


@pytest.fixture
def manager(storage):
    return WebSocketManager(storage)


@pytest.mark.asyncio
async def test_ws_manager_register_and_unregister(manager):
    """Tests that the WebSocketManager can register and unregister connections."""
    ws = AsyncMock(spec=web.WebSocketResponse)

    await manager.register("worker-1", ws)
    assert "worker-1" in manager._connections

    await manager.unregister("worker-1")
    assert "worker-1" not in manager._connections


@pytest.mark.asyncio
async def test_ws_manager_send_command(manager):
    """Tests that the WebSocketManager can send commands to workers."""
    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.closed = False

    await manager.register("worker-1", ws)

    command = {"command": "test"}
    result = await manager.send_command("worker-1", command)

    assert result is True
    ws.send_json.assert_called_with(command)


@pytest.mark.asyncio
async def test_ws_manager_send_command_fails(manager):
    """Tests that send_command returns False when the connection is closed."""
    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.closed = True

    await manager.register("worker-1", ws)

    command = {"command": "test"}
    result = await manager.send_command("worker-1", command)

    assert result is False
    ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_ws_manager_close_all(manager):
    """Tests that the WebSocketManager can close all connections."""
    ws1 = AsyncMock(spec=web.WebSocketResponse)
    ws2 = AsyncMock(spec=web.WebSocketResponse)

    await manager.register("worker-1", ws1)
    await manager.register("worker-2", ws2)

    await manager.close_all()

    ws1.close.assert_called_with(code=1001, message=b"Server shutdown")
    ws2.close.assert_called_with(code=1001, message=b"Server shutdown")
    assert not manager._connections
