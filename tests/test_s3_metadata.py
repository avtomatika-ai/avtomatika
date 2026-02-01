import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from avtomatika.s3 import TaskFiles


# Mock objects for obstore
class MockMeta:
    def __init__(self, size, etag):
        self.size = size
        self.e_tag = etag


class MockResponse:
    def __init__(self, size=100, etag='"abc123hash"'):
        self.meta = MockMeta(size, etag)

    def stream(self):
        async def _stream():
            yield b"some data"

        return _stream()


class MockPutResult:
    def __init__(self, etag='"put_hash_123"'):
        self.e_tag = etag


@pytest.fixture
def mock_store():
    return MagicMock()


@pytest.fixture
def mock_history():
    history = MagicMock()
    history.log_job_event = AsyncMock()
    return history


@pytest.fixture
def task_files(mock_store, mock_history, tmp_path):
    semaphore = asyncio.Semaphore(10)
    return TaskFiles(
        store=mock_store,
        bucket="test-bucket",
        job_id="test-job",
        base_local_dir=tmp_path,
        semaphore=semaphore,
        history=mock_history,
    )


@pytest.mark.asyncio
async def test_upload_single_file_metadata(task_files, tmp_path):
    # Setup local file
    local_file = task_files.local_dir / "test.txt"
    task_files._ensure_local_dir()
    local_file.write_text("content")

    # Mock put_async
    with patch("avtomatika.s3.put_async", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = MockPutResult(etag='"new_etag"')

        # Execute
        uri = await task_files.upload("test.txt")

        # Verify result
        assert uri == "s3://test-bucket/jobs/test-job/test.txt"

        # Verify log event
        task_files._history.log_job_event.assert_called_once()
        call_args = task_files._history.log_job_event.call_args[0][0]
        assert call_args["event_type"] == "s3_operation"
        snapshot = call_args["context_snapshot"]
        assert snapshot["operation"] == "upload"
        assert snapshot["etag"] == "new_etag"
        assert snapshot["size"] == 7  # len("content")


@pytest.mark.asyncio
async def test_download_single_file_metadata(task_files):
    # Mock get_async
    with patch("avtomatika.s3.get_async", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MockResponse(size=123, etag='"remote_etag"')

        # Execute
        await task_files.download("s3://test-bucket/some/file.txt", "downloaded.txt")

        # Verify file creation (mock stream writes "some data")
        downloaded_file = task_files.local_dir / "downloaded.txt"
        assert downloaded_file.exists()
        assert downloaded_file.read_text() == "some data"  # From MockResponse stream

        # Verify log event
        task_files._history.log_job_event.assert_called_once()
        snapshot = task_files._history.log_job_event.call_args[0][0]["context_snapshot"]
        assert snapshot["operation"] == "download"
        assert snapshot["size"] == 123
        assert snapshot["etag"] == "remote_etag"


@pytest.mark.asyncio
async def test_download_size_mismatch(task_files):
    # Mock get_async to return size 50
    with patch("avtomatika.s3.get_async", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MockResponse(size=50)

        # Execute with expected_size=100
        from rxon.exceptions import IntegrityError

        with pytest.raises(IntegrityError, match="File size mismatch"):
            await task_files.download("s3://test-bucket/file.txt", "file.txt", verify_meta={"size": 100})


@pytest.mark.asyncio
async def test_download_directory_metadata(task_files):
    # Mock list to return 2 files
    mock_entries = [{"path": "jobs/test-job/dir/file1.txt"}, {"path": "jobs/test-job/dir/file2.txt"}]

    with (
        patch("avtomatika.s3.obstore_list", return_value=mock_entries),
        patch("avtomatika.s3.get_async", new_callable=AsyncMock) as mock_get,
    ):
        # Mock responses for 2 files
        mock_get.side_effect = [MockResponse(size=10, etag='"etag1"'), MockResponse(size=20, etag='"etag2"')]

        await task_files.download("dir/", "local_dir")

        # Verify log event aggregation
        task_files._history.log_job_event.assert_called_once()
        snapshot = task_files._history.log_job_event.call_args[0][0]["context_snapshot"]
        assert snapshot["operation"] == "download_dir"
        assert snapshot["total_size"] == 30  # 10 + 20
        assert snapshot["file_count"] == 2
