# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from asyncio import Semaphore, gather, to_thread
from datetime import timedelta
from logging import getLogger
from os import sep, walk
from pathlib import Path
from shutil import rmtree
from typing import Any, cast

from orjson import dumps, loads
from rxon.blob import BlobProvider, calculate_config_hash, parse_uri
from rxon.exceptions import IntegrityError

from .config import Config
from .history.base import HistoryStorageBase

logger = getLogger(__name__)

try:
    from aiofiles import open as aiopen
    from obstore import delete_async, get_async, put_async, sign
    from obstore import list as obstore_list
    from obstore.store import S3Store

    HAS_S3_LIBS = True
except ImportError:
    # Define stubs for type hinting and avoid NameErrors
    HAS_S3_LIBS = False
    S3Store = cast(Any, object)
    delete_async = get_async = put_async = sign = obstore_list = aiopen = cast(Any, None)


class TaskFiles:
    """
    Manages files for a specific job, ensuring full compatibility with avtomatika-worker.
    Supports recursive directory download/upload and non-blocking I/O.
    Uses BlobProvider for actual storage access.
    """

    def __init__(
        self,
        provider: BlobProvider,
        bucket: str,
        job_id: str,
        base_local_dir: str | Path,
        history: HistoryStorageBase | None = None,
    ):
        self._provider = provider
        self._bucket = bucket
        self._job_id = job_id
        self._history = history
        self._s3_prefix = f"jobs/{job_id}/"
        self.local_dir = Path(base_local_dir) / job_id

    def _ensure_local_dir(self) -> None:
        if not self.local_dir.exists():
            self.local_dir.mkdir(parents=True, exist_ok=True)

    def path(self, filename: str) -> Path:
        """Returns local path for a filename, ensuring the directory exists."""
        self._ensure_local_dir()
        clean_name = filename.rsplit("/", maxsplit=1)[-1] if "://" in filename else filename.lstrip("/")
        return self.local_dir / clean_name

    def generate_presigned_url(
        self,
        filename: str,
        method: str = "GET",
        expires_in: int = 3600,
    ) -> str:
        """Generates a temporary S3 access link for a file within this job's prefix."""
        target_key = f"{self._s3_prefix}{filename.lstrip('/')}"
        return str(self._provider.generate_presigned_url(target_key, method, expires_in))

    async def download(
        self,
        name_or_uri: str,
        local_name: str | None = None,
        verify_meta: dict[str, Any] | None = None,
    ) -> Path:
        """
        Downloads a file or directory (recursively).
        """
        bucket, key, is_dir = parse_uri(name_or_uri, self._bucket, self._s3_prefix)
        verify_meta = verify_meta or {}

        if local_name:
            target_path = self.path(local_name)
        else:
            suffix = key.replace(self._s3_prefix, "", 1) if key.startswith(self._s3_prefix) else key.split("/")[-1]
            target_path = self.local_dir / suffix

        if is_dir:
            logger.info(f"Recursive download: s3://{bucket}/{key} -> {target_path}")
            entries = await self._provider.list_objects(key)

            tasks = []
            for entry in entries:
                s3_key = entry["path"]
                rel_path = s3_key[len(key) :]
                if not rel_path:
                    continue

                local_file_path = target_path / rel_path
                tasks.append(self._provider.download(f"s3://{bucket}/{s3_key}", str(local_file_path)))

            if tasks:
                results = await gather(*tasks)
                # For simplicity in this refactor, we don't log exact sizes for dirs here
                await self._log_event(
                    "download_dir",
                    f"s3://{bucket}/{key}",
                    str(target_path),
                    metadata={"file_count": len(results)},
                )
            return target_path
        else:
            logger.debug(f"Downloading s3://{bucket}/{key} -> {target_path}")
            success = await self._provider.download(f"s3://{bucket}/{key}", str(target_path))
            if not success:
                raise IntegrityError(f"Failed to download {name_or_uri}")

            meta = await self._provider.get_metadata(f"s3://{bucket}/{key}")
            await self._log_event("download", f"s3://{bucket}/{key}", str(target_path), metadata=meta)
            return target_path

    async def upload(self, local_name: str, remote_name: str | None = None) -> str:
        """
        Uploads a file or directory recursively.
        """
        local_path = self.path(local_name)

        if local_path.is_dir():
            base_remote = (remote_name or local_name).lstrip("/")
            if not base_remote.endswith("/"):
                base_remote += "/"

            target_prefix = f"{self._s3_prefix}{base_remote}"
            logger.info(f"Recursive upload: {local_path} -> s3://{self._bucket}/{target_prefix}")

            def collect_files():
                files_to_upload = []
                for root, _, files in walk(local_path):
                    for file in files:
                        abs_path = Path(root) / file
                        rel_path = abs_path.relative_to(local_path)
                        s3_key = f"{target_prefix}{str(rel_path).replace(sep, '/')}"
                        files_to_upload.append((abs_path, s3_key))
                return files_to_upload

            files_map = await to_thread(collect_files)

            tasks = [self._provider.upload(str(lp), f"s3://{self._bucket}/{k}") for lp, k in files_map]
            if tasks:
                await gather(*tasks)
                metadata = {"file_count": len(tasks)}
            else:
                metadata = {"file_count": 0}

            uri = f"s3://{self._bucket}/{target_prefix}"
            await self._log_event("upload_dir", uri, str(local_path), metadata=metadata)
            return str(uri)

        elif local_path.exists():
            target_key = f"{self._s3_prefix}{(remote_name or local_name).lstrip('/')}"
            logger.debug(f"Uploading {local_path} -> s3://{self._bucket}/{target_key}")

            uri = await self._provider.upload(str(local_path), f"s3://{self._bucket}/{target_key}")
            meta = await self._provider.get_metadata(uri)

            await self._log_event("upload", uri, str(local_path), metadata=meta)
            return str(uri)
        else:
            raise FileNotFoundError(f"Local file/dir not found: {local_path}")

    async def upload_stream(self, filename: str, stream: Any) -> str:
        """Streams data directly to S3."""
        return str(await self._provider.upload_stream(filename, stream, self._s3_prefix))

    async def read_text(self, name_or_uri: str) -> str:
        bucket, key, _ = parse_uri(name_or_uri, self._bucket, self._s3_prefix)
        filename = key.split("/")[-1]
        local_path = self.path(filename)

        if not local_path.exists():
            await self.download(name_or_uri)

        async with aiopen(local_path, "r", encoding="utf-8") as f:
            return str(await f.read())

    async def read_json(self, name_or_uri: str) -> Any:
        bucket, key, _ = parse_uri(name_or_uri, self._bucket, self._s3_prefix)
        filename = key.split("/")[-1]
        local_path = self.path(filename)

        if not local_path.exists():
            await self.download(name_or_uri)

        async with aiopen(local_path, "rb") as f:
            content = await f.read()
            return loads(content)

    async def write_json(self, filename: str, data: Any, upload: bool = True) -> str:
        local_path = self.path(filename)
        json_bytes = dumps(data)

        async with aiopen(local_path, "wb") as f:
            await f.write(json_bytes)

        if upload:
            return str(await self.upload(filename))
        return f"file://{local_path}"

    async def write_text(self, filename: str, text: str, upload: bool = True) -> Path:
        local_path = self.path(filename)
        async with aiopen(local_path, "w", encoding="utf-8") as f:
            await f.write(text)

        if upload:
            await self.upload(filename)

        return local_path

    async def cleanup(self) -> None:
        """Full cleanup of S3 prefix and local job directory."""
        logger.info(f"Cleanup for job {self._job_id}...")
        try:
            entries = await self._provider.list_objects(self._s3_prefix)
            paths_to_delete = [entry["path"] for entry in entries]
            if paths_to_delete:
                await self._provider.delete_objects(paths_to_delete)
        except Exception as e:
            logger.error(f"S3 cleanup error: {e}")

        if self.local_dir.exists():
            await to_thread(rmtree, self.local_dir)

    async def _log_event(
        self,
        operation: str,
        file_uri: str,
        local_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._history:
            return

        try:
            context_snapshot = {
                "operation": operation,
                "s3_uri": file_uri,
                "local_path": str(local_path),
            }
            if metadata:
                context_snapshot.update(metadata)

            await self._history.log_job_event(
                {
                    "job_id": self._job_id,
                    "event_type": "s3_operation",
                    "state": "running",
                    "context_snapshot": context_snapshot,
                }
            )
        except Exception as e:
            logger.warning(f"Failed to log S3 event: {e}")


class S3Service(BlobProvider):
    """
    Central service for S3 operations, implementing RXON BlobProvider.
    Initializes the Store and provides TaskFiles instances.
    """

    def __init__(self, config: Config, history: HistoryStorageBase | None = None):
        self.config = config
        self._history = history
        self._store: S3Store | None = None
        self._semaphore: Semaphore | None = None

        self._config_present = bool(config.S3_ENDPOINT_URL and config.S3_ACCESS_KEY and config.S3_SECRET_KEY)

        if self._config_present:
            if HAS_S3_LIBS:
                self._enabled = True
                self._initialize_store()
            else:
                logger.error(
                    "S3 configuration found, but 'avtomatika[s3]' extra dependencies are not installed. "
                    "S3 support will be disabled. Install with: pip install 'avtomatika[s3]'"
                )
                self._enabled = False
        else:
            self._enabled = False
            if any([config.S3_ENDPOINT_URL, config.S3_ACCESS_KEY, config.S3_SECRET_KEY]):
                logger.warning("Partial S3 configuration found. S3 support disabled.")

    def _initialize_store(self) -> None:
        try:
            self._store = S3Store(
                bucket=self.config.S3_DEFAULT_BUCKET,
                aws_access_key_id=self.config.S3_ACCESS_KEY,
                aws_secret_access_key=self.config.S3_SECRET_KEY,
                region=self.config.S3_REGION,
                endpoint=self.config.S3_ENDPOINT_URL,
                allow_http="http://" in self.config.S3_ENDPOINT_URL,
            )
            self._semaphore = Semaphore(self.config.S3_MAX_CONCURRENCY)
            logger.info(
                f"S3Service initialized (Endpoint: {self.config.S3_ENDPOINT_URL}, "
                f"Bucket: {self.config.S3_DEFAULT_BUCKET}, "
                f"Max Concurrency: {self.config.S3_MAX_CONCURRENCY})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize S3 Store: {e}")
            self._enabled = False

    async def upload(self, local_path: str, uri: str) -> str:
        """Standard BlobProvider upload."""
        if not self._enabled or not self._store or not self._semaphore:
            raise RuntimeError("S3 support is not enabled or initialized.")

        bucket, key, _ = parse_uri(uri, self.config.S3_DEFAULT_BUCKET)
        path = Path(local_path)

        async with self._semaphore:
            async with aiopen(path, "rb") as f:
                content = await f.read()
            await put_async(self._store, key, content)
            return f"s3://{bucket}/{key}"

    async def download(self, uri: str, local_path: str) -> bool:
        """Standard BlobProvider download."""
        if not self._enabled or not self._store or not self._semaphore:
            return False

        bucket, key, _ = parse_uri(uri, self.config.S3_DEFAULT_BUCKET)
        path = Path(local_path)

        if not path.parent.exists():
            await to_thread(path.parent.mkdir, parents=True, exist_ok=True)

        async with self._semaphore:
            response = await get_async(self._store, key)
            stream = response.stream()
            async with aiopen(path, "wb") as f:
                async for chunk in stream:
                    await f.write(chunk)
            return True

    async def get_metadata(self, uri: str) -> dict[str, Any] | None:
        """Standard BlobProvider metadata."""
        if not self._enabled or not self._store or not self._semaphore:
            return None

        bucket, key, _ = parse_uri(uri, self.config.S3_DEFAULT_BUCKET)
        try:
            async with self._semaphore:
                response = await get_async(self._store, key)
                meta = response.meta
                return {
                    "size": meta.size,
                    "etag": meta.e_tag.strip('"') if meta.e_tag else None,
                    "content_type": getattr(meta, "content_type", None),
                }
        except Exception:
            return None

    async def delete(self, uri: str) -> bool:
        """Deletes a single object from storage."""
        if not self._enabled or not self._store:
            return False
        bucket, key, _ = parse_uri(uri, self.config.S3_DEFAULT_BUCKET)
        try:
            await delete_async(self._store, [key])
            return True
        except Exception as e:
            logger.error(f"Failed to delete S3 object {uri}: {e}")
            return False

    async def delete_dir(self, uri: str) -> bool:
        """Deletes all objects under a specific URI prefix."""
        if not self._enabled or not self._store:
            return False
        bucket, key, _ = parse_uri(uri, self.config.S3_DEFAULT_BUCKET)
        try:
            # obstore doesn't have a direct delete_dir, we must list and then delete
            entries = await self.list_objects(key)
            paths_to_delete = [entry["path"] for entry in entries]
            if paths_to_delete:
                await delete_async(self._store, paths_to_delete)
            return True
        except Exception as e:
            logger.error(f"Failed to delete S3 directory {uri}: {e}")
            return False

    async def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        """Helper for recursive operations."""
        if not self._enabled or not self._store:
            return []
        return await to_thread(lambda: list(obstore_list(self._store, prefix=prefix)))

    async def delete_objects(self, keys: list[str]) -> None:
        """Helper for cleanup."""
        if not self._enabled or not self._store:
            return
        await delete_async(self._store, keys)

    async def upload_stream(self, filename: str, stream: Any, prefix: str) -> str:
        """Helper for streaming upload."""
        if not self._enabled or not self._store or not self._semaphore:
            raise RuntimeError("S3 support is not enabled.")

        target_key = f"{prefix}{filename.lstrip('/')}"
        async with self._semaphore:
            await put_async(self._store, target_key, stream)
            return f"s3://{self.config.S3_DEFAULT_BUCKET}/{target_key}"

    def get_config_hash(self) -> str | None:
        """Returns a hash of the current S3 configuration for consistency checks."""
        if not self._enabled:
            return None
        return cast(
            str | None,
            calculate_config_hash(
                self.config.S3_ENDPOINT_URL,
                self.config.S3_ACCESS_KEY,
                self.config.S3_DEFAULT_BUCKET,
            ),
        )

    def get_task_files(self, job_id: str) -> TaskFiles | None:
        if not self._enabled or not self._store or not self._semaphore:
            return None

        return TaskFiles(
            self,
            self.config.S3_DEFAULT_BUCKET,
            job_id,
            self.config.TASK_FILES_DIR,
            self._history,
        )

    def generate_presigned_url(self, key: str, method: str = "GET", expires_in: int = 3600) -> str:
        """Generates a temporary S3 access link for an arbitrary key."""
        if not self._enabled or not self._store:
            raise RuntimeError("S3 support is not enabled or initialized.")
        return cast(str, sign(self._store, method, key, timedelta(seconds=expires_in)))

    async def close(self) -> None:
        pass
