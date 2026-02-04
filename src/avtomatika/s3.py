from asyncio import Semaphore, gather, to_thread
from logging import getLogger
from os import sep, walk
from pathlib import Path
from shutil import rmtree
from typing import Any

from aiofiles import open as aiopen
from obstore import delete_async, get_async, put_async
from obstore import list as obstore_list
from obstore.store import S3Store
from orjson import dumps, loads
from rxon.blob import calculate_config_hash, parse_uri
from rxon.exceptions import IntegrityError

from .config import Config
from .history.base import HistoryStorageBase

logger = getLogger(__name__)

try:
    HAS_S3_LIBS = True
except ImportError:
    HAS_S3_LIBS = False
    S3Store = Any


class TaskFiles:
    """
    Manages files for a specific job, ensuring full compatibility with avtomatika-worker.
    Supports recursive directory download/upload and non-blocking I/O.
    """

    def __init__(
        self,
        store: "S3Store",
        bucket: str,
        job_id: str,
        base_local_dir: str | Path,
        semaphore: Semaphore,
        history: HistoryStorageBase | None = None,
    ):
        self._store = store
        self._bucket = bucket
        self._job_id = job_id
        self._history = history
        self._s3_prefix = f"jobs/{job_id}/"
        self.local_dir = Path(base_local_dir) / job_id
        self._semaphore = semaphore

    def _ensure_local_dir(self) -> None:
        if not self.local_dir.exists():
            self.local_dir.mkdir(parents=True, exist_ok=True)

    def path(self, filename: str) -> Path:
        """Returns local path for a filename, ensuring the directory exists."""
        self._ensure_local_dir()
        clean_name = filename.split("/")[-1] if "://" in filename else filename.lstrip("/")
        return self.local_dir / clean_name

    async def _download_single_file(
        self,
        key: str,
        local_path: Path,
        expected_size: int | None = None,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Downloads a single file safely using semaphore and streaming.
        Returns metadata (size, etag).
        """
        if not local_path.parent.exists():
            await to_thread(local_path.parent.mkdir, parents=True, exist_ok=True)

        async with self._semaphore:
            response = await get_async(self._store, key)
            meta = response.meta
            file_size = meta.size
            etag = meta.e_tag.strip('"') if meta.e_tag else None

            if expected_size is not None and file_size != expected_size:
                raise IntegrityError(f"File size mismatch for {key}: expected {expected_size}, got {file_size}")

            if expected_hash is not None and etag and expected_hash != etag:
                raise IntegrityError(f"Integrity mismatch for {key}: expected ETag {expected_hash}, got {etag}")

            stream = response.stream()
            async with aiopen(local_path, "wb") as f:
                async for chunk in stream:
                    await f.write(chunk)

            return {"size": file_size, "etag": etag}

    async def download(
        self,
        name_or_uri: str,
        local_name: str | None = None,
        verify_meta: dict[str, Any] | None = None,
    ) -> Path:
        """
        Downloads a file or directory (recursively).
        If URI ends with '/', it treats it as a directory.
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
            entries = await to_thread(lambda: list(obstore_list(self._store, prefix=key)))

            tasks = []
            for entry in entries:
                s3_key = entry["path"]
                rel_path = s3_key[len(key) :]
                if not rel_path:
                    continue

                local_file_path = target_path / rel_path
                tasks.append(self._download_single_file(s3_key, local_file_path))

            if tasks:
                results = await gather(*tasks)
                total_size = sum(r["size"] for r in results)
                await self._log_event(
                    "download_dir",
                    f"s3://{bucket}/{key}",
                    str(target_path),
                    metadata={"total_size": total_size, "file_count": len(results)},
                )
            else:
                await self._log_event(
                    "download_dir",
                    f"s3://{bucket}/{key}",
                    str(target_path),
                    metadata={"total_size": 0, "file_count": 0},
                )
            return target_path
        else:
            logger.debug(f"Downloading s3://{bucket}/{key} -> {target_path}")
            meta = await self._download_single_file(
                key,
                target_path,
                expected_size=verify_meta.get("size"),
                expected_hash=verify_meta.get("hash"),
            )
            await self._log_event("download", f"s3://{bucket}/{key}", str(target_path), metadata=meta)
            return target_path

    async def _upload_single_file(self, local_path: Path, s3_key: str) -> dict[str, Any]:
        """Uploads a single file safely using semaphore. Returns S3 metadata."""
        async with self._semaphore:
            file_size = local_path.stat().st_size
            async with aiopen(local_path, "rb") as f:
                content = await f.read()
            result = await put_async(self._store, s3_key, content)
            etag = result.e_tag.strip('"') if result.e_tag else None
            return {"size": file_size, "etag": etag}

    async def upload(self, local_name: str, remote_name: str | None = None) -> str:
        """
        Uploads a file or directory recursively.
        If local_name points to a directory, it uploads all contents.
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

            tasks = [self._upload_single_file(lp, k) for lp, k in files_map]
            if tasks:
                results = await gather(*tasks)
                total_size = sum(r["size"] for r in results)
                metadata = {"total_size": total_size, "file_count": len(results)}
            else:
                metadata = {"total_size": 0, "file_count": 0}

            uri = f"s3://{self._bucket}/{target_prefix}"
            await self._log_event("upload_dir", uri, str(local_path), metadata=metadata)
            return uri

        elif local_path.exists():
            target_key = f"{self._s3_prefix}{(remote_name or local_name).lstrip('/')}"
            logger.debug(f"Uploading {local_path} -> s3://{self._bucket}/{target_key}")

            meta = await self._upload_single_file(local_path, target_key)

            uri = f"s3://{self._bucket}/{target_key}"
            await self._log_event("upload", uri, str(local_path), metadata=meta)
            return uri
        else:
            raise FileNotFoundError(f"Local file/dir not found: {local_path}")

    async def read_text(self, name_or_uri: str) -> str:
        bucket, key, _ = parse_uri(name_or_uri, self._bucket, self._s3_prefix)
        filename = key.split("/")[-1]
        local_path = self.path(filename)

        if not local_path.exists():
            await self.download(name_or_uri)

        async with aiopen(local_path, "r", encoding="utf-8") as f:
            return await f.read()

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
        """Writes JSON locally (binary mode) and optionally uploads to S3."""
        local_path = self.path(filename)
        json_bytes = dumps(data)

        async with aiopen(local_path, "wb") as f:
            await f.write(json_bytes)

        if upload:
            return await self.upload(filename)
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
            entries = await to_thread(lambda: list(obstore_list(self._store, prefix=self._s3_prefix)))
            paths_to_delete = [entry["path"] for entry in entries]
            if paths_to_delete:
                await delete_async(self._store, paths_to_delete)
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


class S3Service:
    """
    Central service for S3 operations.
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

    def get_config_hash(self) -> str | None:
        """Returns a hash of the current S3 configuration for consistency checks."""
        if not self._enabled:
            return None
        return calculate_config_hash(
            self.config.S3_ENDPOINT_URL,
            self.config.S3_ACCESS_KEY,
            self.config.S3_DEFAULT_BUCKET,
        )

    def get_task_files(self, job_id: str) -> TaskFiles | None:
        if not self._enabled or not self._store or not self._semaphore:
            return None

        return TaskFiles(
            self._store,
            self.config.S3_DEFAULT_BUCKET,
            job_id,
            self.config.TASK_FILES_DIR,
            self._semaphore,
            self._history,
        )

    async def close(self) -> None:
        pass
