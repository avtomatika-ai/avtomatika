import pytest

from avtomatika.blueprint import StateMachineBlueprint
from avtomatika.config import Config
from avtomatika.s3 import S3Service


@pytest.fixture
def s3_config():
    config = Config()
    config.S3_ENDPOINT_URL = "http://localhost:9000"
    config.S3_ACCESS_KEY = "minioadmin"
    config.S3_SECRET_KEY = "minioadmin"
    config.S3_DEFAULT_BUCKET = "test-bucket"
    return config


def test_generate_presigned_url_logic(s3_config):
    service = S3Service(s3_config)
    assert service._enabled

    task_files = service.get_task_files("job-123")
    assert task_files is not None

    # We don't need a real S3 server because sign() is a local cryptographic operation
    url = task_files.generate_presigned_url("output.json", method="PUT", expires_in=60)

    assert "http://localhost:9000/test-bucket/jobs/job-123/output.json" in url
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=60" in url
    assert "PUT" not in url  # Method is usually not in the query string but used for signature calculation


def create_dummy_bp():
    bp = StateMachineBlueprint("dummy", api_endpoint="/dummy", api_version="v1")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        pass

    return bp


@pytest.mark.parametrize("app", [{"extra_blueprints": [create_dummy_bp()]}], indirect=True)
@pytest.mark.asyncio
async def test_presign_handler(aiohttp_client, app, s3_config):
    from avtomatika.app_keys import ENGINE_KEY, S3_SERVICE_KEY
    from avtomatika.s3 import S3Service

    # Enable S3 in the app's engine config
    app_engine = app[ENGINE_KEY]
    app_engine.config.S3_ENDPOINT_URL = s3_config.S3_ENDPOINT_URL
    app_engine.config.S3_ACCESS_KEY = s3_config.S3_ACCESS_KEY
    app_engine.config.S3_SECRET_KEY = s3_config.S3_SECRET_KEY
    app_engine.config.S3_DEFAULT_BUCKET = s3_config.S3_DEFAULT_BUCKET

    # Re-initialize S3Service in app
    app[S3_SERVICE_KEY] = S3Service(app_engine.config, app_engine.history_storage)

    client = await aiohttp_client(app)

    # Mock auth for client
    headers = {"X-Client-Token": "user_token_vip"}

    job_id = "test-job-upload"
    resp = await client.get(f"/api/v1/jobs/{job_id}/files/upload?filename=data.csv&expires_in=100", headers=headers)

    assert resp.status == 200
    data = await resp.json()
    assert "url" in data
    assert data["expires_in"] == 100
    assert data["method"] == "PUT"
    assert f"/test-bucket/jobs/{job_id}/data.csv" in data["url"]


@pytest.mark.parametrize("app", [{"extra_blueprints": [create_dummy_bp()]}], indirect=True)
@pytest.mark.asyncio
async def test_download_redirect_handler(aiohttp_client, app, s3_config):
    from avtomatika.app_keys import ENGINE_KEY, S3_SERVICE_KEY
    from avtomatika.s3 import S3Service

    app_engine = app[ENGINE_KEY]
    app_engine.config.S3_ENDPOINT_URL = s3_config.S3_ENDPOINT_URL
    app_engine.config.S3_ACCESS_KEY = s3_config.S3_ACCESS_KEY
    app_engine.config.S3_SECRET_KEY = s3_config.S3_SECRET_KEY
    app_engine.config.S3_DEFAULT_BUCKET = s3_config.S3_DEFAULT_BUCKET

    app[S3_SERVICE_KEY] = S3Service(app_engine.config, app_engine.history_storage)

    client = await aiohttp_client(app)
    headers = {"X-Client-Token": "user_token_vip"}

    job_id = "test-job-download"
    filename = "result.mp4"

    # Request redirect, disable auto-following redirects to check status code
    resp = await client.get(f"/api/v1/jobs/{job_id}/files/download/{filename}", headers=headers, allow_redirects=False)

    assert resp.status == 302
    location = resp.headers["Location"]
    assert f"/test-bucket/jobs/{job_id}/{filename}" in location
    assert "X-Amz-Signature=" in location


@pytest.mark.parametrize("app", [{"extra_blueprints": [create_dummy_bp()]}], indirect=True)
@pytest.mark.asyncio
async def test_streaming_upload_handler(aiohttp_client, app, s3_config):
    from unittest.mock import AsyncMock, MagicMock, patch

    from avtomatika.app_keys import ENGINE_KEY, S3_SERVICE_KEY
    from avtomatika.s3 import S3Service

    app_engine = app[ENGINE_KEY]
    app_engine.config.S3_ENDPOINT_URL = s3_config.S3_ENDPOINT_URL
    app_engine.config.S3_ACCESS_KEY = s3_config.S3_ACCESS_KEY
    app_engine.config.S3_SECRET_KEY = s3_config.S3_SECRET_KEY
    app_engine.config.S3_DEFAULT_BUCKET = s3_config.S3_DEFAULT_BUCKET

    app[S3_SERVICE_KEY] = S3Service(app_engine.config, app_engine.history_storage)

    client = await aiohttp_client(app)
    headers = {"X-Client-Token": "user_token_vip", "Content-Type": "application/octet-stream"}

    job_id = "test-job-stream"
    filename = "input.data"
    payload = b"some binary content"

    # Mock obstore.put_async
    mock_put_result = MagicMock()
    mock_put_result.e_tag = "some-etag"

    with patch("avtomatika.s3.put_async", AsyncMock(return_value=mock_put_result)) as mock_put:
        resp = await client.put(f"/api/v1/jobs/{job_id}/files/content/{filename}", data=payload, headers=headers)

        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "uploaded"
        assert f"s3://{s3_config.S3_DEFAULT_BUCKET}/jobs/{job_id}/{filename}" in data["s3_uri"]
        mock_put.assert_called_once()
