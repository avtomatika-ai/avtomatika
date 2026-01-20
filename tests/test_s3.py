import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from avtomatika.config import Config
from avtomatika.s3 import S3Service, TaskFiles


@pytest.fixture
def config(tmp_path):
    c = Config()
    c.S3_ENDPOINT_URL = "http://localhost:9000"
    c.S3_ACCESS_KEY = "minio"
    c.S3_SECRET_KEY = "minio123"
    c.S3_DEFAULT_BUCKET = "test-bucket"
    c.TASK_FILES_DIR = str(tmp_path / "payloads")
    c.S3_MAX_CONCURRENCY = 10
    return c


@pytest.mark.asyncio
async def test_s3_service_initialization(config):
    with patch("avtomatika.s3.S3Store") as MockS3Store:
        service = S3Service(config)
        assert service._enabled is True
        assert service._semaphore is not None
        assert service._semaphore._value == 10
        MockS3Store.assert_called_once()


@pytest.mark.asyncio
async def test_s3_service_disabled_without_config():
    c = Config()
    c.S3_ENDPOINT_URL = ""
    service = S3Service(c)
    assert service._enabled is False
    assert service.get_task_files("job1") is None


@pytest.mark.asyncio
async def test_task_files_local_isolation(config):
    mock_store = MagicMock()
    job_id = "job-123"
    sem = asyncio.Semaphore(1)
    tf = TaskFiles(mock_store, "bucket", job_id, config.TASK_FILES_DIR, sem)

    expected_path = Path(config.TASK_FILES_DIR) / job_id
    assert tf.local_dir == expected_path

    file_path = tf.path("test.txt")
    assert file_path == expected_path / "test.txt"
    assert expected_path.exists()


@pytest.mark.asyncio
async def test_task_files_sync_operations(config):
    mock_store = MagicMock()
    job_id = "job-sync"
    mock_history = AsyncMock()
    sem = asyncio.Semaphore(1)
    tf = TaskFiles(mock_store, "bucket", job_id, config.TASK_FILES_DIR, sem, mock_history)

    with (
        patch("avtomatika.s3.get_async", AsyncMock()) as mock_get,
        patch("avtomatika.s3.put_async", AsyncMock()) as mock_put,
        patch("avtomatika.s3.aiopen", MagicMock()) as mock_aio_open,
    ):
        # Mock streaming download
        mock_resp = MagicMock()

        async def mock_stream_gen():
            yield b"remote "
            yield b"content"

        mock_resp.stream.return_value = mock_stream_gen()
        mock_get.return_value = mock_resp

        mock_file = AsyncMock()
        mock_file.read.return_value = b"local content"
        mock_aio_open.return_value.__aenter__.return_value = mock_file

        # Test download
        await tf.download("remote.dat")
        mock_get.assert_called_once()
        # Verify write was called (chunks are concatenated or written sequentially)
        assert mock_file.write.call_count >= 1

        # Test upload
        with patch("pathlib.Path.exists", return_value=True):
            await tf.upload("local.dat")
            mock_put.assert_called_once()


@pytest.mark.asyncio
async def test_full_cleanup(config, tmp_path):
    mock_store = MagicMock()
    job_id = "job-cleanup"
    sem = asyncio.Semaphore(1)
    tf = TaskFiles(mock_store, "bucket", job_id, config.TASK_FILES_DIR, sem)

    local_file = tf.path("to_delete.txt")
    with open(local_file, "w") as f:
        f.write("hello")
    assert tf.local_dir.exists()

    with (
        patch("avtomatika.s3.obstore_list", MagicMock(return_value=[{"path": f"jobs/{job_id}/s3.txt"}])),
        patch("avtomatika.s3.delete_async", AsyncMock()) as mock_delete,
    ):
        await tf.cleanup()

        # Now expects a LIST of paths
        mock_delete.assert_called_with(mock_store, [f"jobs/{job_id}/s3.txt"])
        assert not tf.local_dir.exists()


@pytest.mark.asyncio
async def test_task_files_helper_methods(config):
    mock_store = MagicMock()
    sem = asyncio.Semaphore(1)
    tf = TaskFiles(mock_store, "test-bucket", "job1", config.TASK_FILES_DIR, sem)

    with (
        patch("avtomatika.s3.get_async", AsyncMock()) as mock_get,
        patch("pathlib.Path.exists", return_value=False),
        patch("avtomatika.s3.aiopen", MagicMock()) as mock_aio_open,
    ):
        # Helper methods like read_text call download() internally
        mock_resp = MagicMock()

        async def mock_stream_gen():
            yield b'{"key": "value"}'

        mock_resp.stream.return_value = mock_stream_gen()
        mock_get.return_value = mock_resp

        mock_file = AsyncMock()
        # Side effect for read_text/read_json after download
        mock_file.read.side_effect = ['{"key": "value"}', b'{"key": "value"}']
        mock_file.write = AsyncMock()  # Mock write for the download part
        mock_aio_open.return_value.__aenter__.return_value = mock_file

        text = await tf.read_text("data.json")
        assert text == '{"key": "value"}'

        data = await tf.read_json("data.json")
        assert data == {"key": "value"}

    with (
        patch("avtomatika.s3.put_async", AsyncMock()) as mock_put,
        patch("pathlib.Path.exists", return_value=True),
        patch("avtomatika.s3.aiopen", MagicMock()) as mock_aio_open,
    ):
        mock_file = AsyncMock()
        mock_file.read.return_value = b"hello"
        mock_aio_open.return_value.__aenter__.return_value = mock_file

        await tf.write_text("out.txt", "hello")
        mock_put.assert_called_with(mock_store, "jobs/job1/out.txt", b"hello")


@pytest.mark.asyncio
async def test_recursive_download(config):
    mock_store = MagicMock()
    job_id = "rec-down"
    sem = asyncio.Semaphore(5)
    tf = TaskFiles(mock_store, "bucket", job_id, config.TASK_FILES_DIR, sem)

    with (
        patch(
            "avtomatika.s3.obstore_list",
            return_value=[{"path": f"jobs/{job_id}/data/file1.txt"}, {"path": f"jobs/{job_id}/data/sub/file2.txt"}],
        ),
        patch("avtomatika.s3.get_async", AsyncMock()) as mock_get,
        patch("avtomatika.s3.aiopen", MagicMock()) as mock_open,
    ):
        mock_resp = MagicMock()

        async def mock_stream_gen():
            yield b"content"

        mock_resp.stream.return_value = mock_stream_gen()
        mock_get.return_value = mock_resp

        # We also need to mock file writing
        mock_file = AsyncMock()
        mock_open.return_value.__aenter__.return_value = mock_file

        await tf.download("data/")
        assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_recursive_upload(config):
    mock_store = MagicMock()
    job_id = "rec-up"
    sem = asyncio.Semaphore(5)
    tf = TaskFiles(mock_store, "bucket", job_id, config.TASK_FILES_DIR, sem)
    local_dir = tf.path("data")
    walk_data = [(str(local_dir), [], ["file1.txt"]), (str(local_dir / "sub"), [], ["file2.txt"])]

    with (
        patch("avtomatika.s3.walk", return_value=walk_data),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("pathlib.Path.exists", return_value=True),
        patch("avtomatika.s3.put_async", AsyncMock()) as mock_put,
        patch("avtomatika.s3.aiopen", MagicMock()),
    ):
        await tf.upload("data")
        assert mock_put.call_count == 2


@pytest.mark.asyncio
async def test_job_executor_s3_injection(config):
    from avtomatika.app_keys import S3_SERVICE_KEY
    from avtomatika.blueprint import StateMachineBlueprint
    from avtomatika.engine import OrchestratorEngine
    from avtomatika.executor import JobExecutor

    mock_storage = AsyncMock()
    mock_history = AsyncMock()
    engine = OrchestratorEngine(mock_storage, config)
    # Mock webhook sender to prevent errors during transition/failure handling
    engine.webhook_sender = AsyncMock()
    engine.webhook_sender.start = MagicMock()  # start() is sync, avoids RuntimeWarning
    # Mock dispatcher to avoid init issues
    engine.dispatcher = MagicMock()

    mock_s3 = MagicMock()
    mock_task_files = MagicMock()
    mock_s3.get_task_files.return_value = mock_task_files
    engine.app[S3_SERVICE_KEY] = mock_s3

    bp = StateMachineBlueprint("s3_test")
    handler_called = False

    @bp.handler_for("start", is_start=True)
    async def s3_handler(task_files, actions):
        nonlocal handler_called
        assert task_files is mock_task_files
        handler_called = True
        actions.transition_to("finished")

    @bp.handler_for("finished", is_end=True)
    async def end_handler():
        pass

    engine.register_blueprint(bp)
    executor = JobExecutor(engine, mock_history)

    job_id = "test-job-s3"
    job_state = {
        "id": job_id,
        "blueprint_name": "s3_test",
        "current_state": "start",
        "status": "pending",
        "initial_data": {},
        "client_config": {},
    }
    mock_storage.get_job_state.return_value = job_state

    await executor._process_job(job_id, "msg-1")
    assert handler_called is True
