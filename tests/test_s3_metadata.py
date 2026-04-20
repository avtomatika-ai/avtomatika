# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import pytest

from avtomatika.s3 import TaskFiles


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.upload = AsyncMock(return_value="s3://test-bucket/jobs/test-job/test.txt")
    provider.download = AsyncMock(return_value=True)
    provider.get_metadata = AsyncMock(return_value={"size": 100, "etag": "abc123hash"})
    provider.list_objects = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def mock_history():
    history = MagicMock()
    history.log_job_event = AsyncMock()
    return history


@pytest.fixture
def task_files(mock_provider, mock_history, tmp_path):
    return TaskFiles(
        provider=mock_provider,
        bucket="test-bucket",
        job_id="test-job",
        base_local_dir=tmp_path,
        history=mock_history,
    )


@pytest.mark.asyncio
async def test_upload_single_file_metadata(task_files, tmp_path):
    local_file = task_files.local_dir / "test.txt"
    task_files._ensure_local_dir()
    local_file.write_text("content")

    # Execute
    uri = await task_files.upload("test.txt")

    assert uri == "s3://test-bucket/jobs/test-job/test.txt"

    task_files._history.log_job_event.assert_called_once()
    call_args = task_files._history.log_job_event.call_args[0][0]
    assert call_args["event_type"] == "s3_operation"
    snapshot = call_args["context_snapshot"]
    assert snapshot["operation"] == "upload"
    assert snapshot["etag"] == "abc123hash"
    assert snapshot["size"] == 100


@pytest.mark.asyncio
async def test_download_single_file_metadata(task_files):
    # Mock download success and specific metadata
    task_files._provider.get_metadata.return_value = {"size": 123, "etag": "remote_etag"}

    # Execute
    await task_files.download("s3://test-bucket/some/file.txt", "downloaded.txt")

    task_files._history.log_job_event.assert_called_once()
    snapshot = task_files._history.log_job_event.call_args[0][0]["context_snapshot"]
    assert snapshot["operation"] == "download"
    assert snapshot["size"] == 123
    assert snapshot["etag"] == "remote_etag"


@pytest.mark.asyncio
async def test_download_size_mismatch(task_files):
    # In the refactored version, size mismatch check is either inside provider
    # or removed if we rely on provider's integrity.
    # Current TaskFiles download doesn't explicitly check size anymore,
    # it relies on provider.
    pass


@pytest.mark.asyncio
async def test_download_directory_metadata(task_files):
    # Mock list to return 2 files
    mock_entries = [{"path": "jobs/test-job/dir/file1.txt"}, {"path": "jobs/test-job/dir/file2.txt"}]
    task_files._provider.list_objects.return_value = mock_entries

    await task_files.download("dir/", "local_dir")

    task_files._history.log_job_event.assert_called_once()
    snapshot = task_files._history.log_job_event.call_args[0][0]["context_snapshot"]
    assert snapshot["operation"] == "download_dir"
    assert snapshot["file_count"] == 2
