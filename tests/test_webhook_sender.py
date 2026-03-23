# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from src.avtomatika.utils.webhook_sender import WebhookPayload, WebhookSender


@pytest.mark.asyncio
async def test_webhook_sender_queues_message():
    mock_session = AsyncMock(spec=ClientSession)
    sender = WebhookSender(mock_session)
    payload = WebhookPayload(
        event="job_finished",
        job_id="test-job-1",
        status="finished",
        result={"foo": "bar"},
    )

    await sender.send("http://example.com/webhook", payload)

    assert sender._queue.qsize() == 1
    item = await sender._queue.get()
    assert item == ("http://example.com/webhook", payload)


@pytest.mark.asyncio
async def test_webhook_sender_send_logic_success():
    """Test the internal sending logic (_send_single)."""
    mock_session = AsyncMock(spec=ClientSession)
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_session.post.return_value.__aenter__.return_value = mock_response

    sender = WebhookSender(mock_session)
    payload = WebhookPayload(
        event="job_finished",
        job_id="test-job-1",
        status="finished",
        result={"foo": "bar"},
    )

    success = await sender._send_single("http://example.com/webhook", payload)
    assert success is True
    mock_session.post.assert_called_once()
    args, kwargs = mock_session.post.call_args
    assert args[0] == "http://example.com/webhook"
    assert kwargs["json"]["event"] == "job_finished"


@pytest.mark.asyncio
async def test_webhook_sender_retry_failure(mocker):
    """Test retry logic in _send_single."""
    mock_session = AsyncMock(spec=ClientSession)
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_session.post.return_value.__aenter__.return_value = mock_response

    sender = WebhookSender(mock_session)
    sender.max_retries = 2

    # Patch asyncio.sleep where it is used (in the module) to avoid waiting during tests
    mock_sleep = mocker.patch("src.avtomatika.utils.webhook_sender.sleep", new_callable=AsyncMock)

    payload = WebhookPayload(
        event="job_failed",
        job_id="test-job-2",
        status="failed",
        error="Something went wrong",
    )

    success = await sender._send_single("http://example.com/webhook", payload)
    assert success is False
    assert mock_session.post.call_count == 2
    assert mock_sleep.call_count == 1  # Called once between retry 1 and 2


@pytest.mark.asyncio
async def test_webhook_sender_concurrency():
    """Checks that WebhookSender can process multiple webhooks concurrently."""
    import asyncio
    import time

    mock_session = AsyncMock(spec=ClientSession)
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.release = AsyncMock()

    # Properly mock async context manager for post with delay
    class MockPostContext:
        async def __aenter__(self):
            await asyncio.sleep(0.2)
            return mock_response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.post.side_effect = lambda *args, **kwargs: MockPostContext()

    sender = WebhookSender(mock_session, worker_count=5)
    sender.start()

    start_time = time.time()
    payload = WebhookPayload(event="test", job_id="j1", status="ok")

    # Send 5 webhooks
    for i in range(5):
        await sender.send(f"http://test{i}.com", payload)

    # Wait for them to be processed
    await sender._queue.join()
    duration = time.time() - start_time

    # If they were sequential, it would take 5 * 0.2 = 1.0s
    # With 5 workers, it should take ~0.2s (+ overhead)
    assert duration < 0.5
    assert mock_session.post.call_count == 5

    await sender.stop()
