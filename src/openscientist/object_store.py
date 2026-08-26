"""Content-addressed object storage for governed scientific state."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1500}$")


class ObjectStoreError(RuntimeError):
    """Object storage failed or returned content with the wrong identity."""


class ObjectStore(Protocol):
    def put(
        self,
        key: str,
        content: bytes,
        *,
        sha256: str,
        content_type: str | None = None,
    ) -> None: ...

    def get(self, key: str, *, sha256: str) -> bytes: ...


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_key(key: str) -> None:
    if not _SAFE_KEY.fullmatch(key) or key.startswith("/") or ".." in key.split("/"):
        raise ObjectStoreError("Object key must be a safe relative path.")


def _validate_identity(key: str, content: bytes, expected_sha256: str) -> None:
    _validate_key(key)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ObjectStoreError("Object SHA-256 identity is invalid.")
    if content_sha256(content) != expected_sha256:
        raise ObjectStoreError("Object content does not match its SHA-256 identity.")


class FilesystemObjectStore:
    """Atomic content-addressed storage under a persistent filesystem root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ObjectStoreError("Object key escapes the configured storage root.")
        return path

    def put(
        self,
        key: str,
        content: bytes,
        *,
        sha256: str,
        content_type: str | None = None,
    ) -> None:
        _validate_identity(key, content, sha256)
        path = self._path(key)
        if path.is_file():
            existing = path.read_bytes()
            if content_sha256(existing) != sha256:
                raise ObjectStoreError("Existing object content conflicts with its key.")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the temporary basename short so atomic writes also work under
        # Windows' legacy MAX_PATH limit when the content key is long.
        temporary = path.with_name(f".tmp-{uuid4().hex}")
        try:
            temporary.write_bytes(content)
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = path.read_bytes()
                if content_sha256(existing) != sha256:
                    raise ObjectStoreError(
                        "Existing object content conflicts with its key."
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str, *, sha256: str) -> bytes:
        path = self._path(key)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ObjectStoreError(f"Object is unavailable: {key}") from exc
        _validate_identity(key, content, sha256)
        return content


class S3ObjectStore:
    """Content-addressed S3-compatible object storage."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "openscientist",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        server_side_encryption: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
    ) -> None:
        import boto3  # type: ignore[import-untyped]

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.server_side_encryption = server_side_encryption
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
        )

    def _key(self, key: str) -> str:
        _validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(
        self,
        key: str,
        content: bytes,
        *,
        sha256: str,
        content_type: str | None = None,
    ) -> None:
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        _validate_identity(key, content, sha256)
        object_key = self._key(key)
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in {403, 404}:
                raise ObjectStoreError("S3 object lookup failed.") from exc
        else:
            metadata_sha256 = response.get("Metadata", {}).get("sha256")
            if metadata_sha256 != sha256 or response.get("ContentLength") != len(content):
                raise ObjectStoreError("Existing S3 object conflicts with its identity.")
            return
        arguments: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Body": content,
            "Metadata": {"sha256": sha256},
            "ContentType": content_type or "application/octet-stream",
        }
        if self.server_side_encryption:
            arguments["ServerSideEncryption"] = self.server_side_encryption
        try:
            self.client.put_object(**arguments)
        except ClientError as exc:
            raise ObjectStoreError("S3 object write failed.") from exc

    def get(self, key: str, *, sha256: str) -> bytes:
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            content = response["Body"].read()
        except (ClientError, KeyError, OSError) as exc:
            raise ObjectStoreError(f"S3 object is unavailable: {key}") from exc
        if not isinstance(content, bytes):
            raise ObjectStoreError("S3 returned a non-byte object body.")
        _validate_identity(key, content, sha256)
        return content


def configured_object_store() -> ObjectStore:
    """Construct the configured durable object backend."""

    from openscientist.settings import get_settings

    settings = get_settings().object_storage
    if settings.backend == "s3":
        assert settings.s3_bucket is not None
        return S3ObjectStore(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            server_side_encryption=settings.s3_server_side_encryption,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            session_token=settings.s3_session_token,
        )
    return FilesystemObjectStore(Path(settings.filesystem_root))
