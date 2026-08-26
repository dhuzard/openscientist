from __future__ import annotations

from io import BytesIO

import pytest

from openscientist.object_store import (
    FilesystemObjectStore,
    ObjectStoreError,
    S3ObjectStore,
    content_sha256,
)


def test_filesystem_object_store_is_content_addressed_and_idempotent(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    content = b'{"result":42}\n'
    digest = content_sha256(content)

    store.put("jobs/job-1/evidence/result.json", content, sha256=digest)
    store.put("jobs/job-1/evidence/result.json", content, sha256=digest)

    assert store.get("jobs/job-1/evidence/result.json", sha256=digest) == content


def test_filesystem_object_store_rejects_bad_identity_and_unsafe_keys(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")

    with pytest.raises(ObjectStoreError, match="does not match"):
        store.put("jobs/job-1/result.json", b"content", sha256="0" * 64)
    with pytest.raises(ObjectStoreError, match="safe relative path"):
        store.put("../result.json", b"content", sha256=content_sha256(b"content"))


def test_filesystem_object_store_detects_corruption(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    content = b"governed evidence"
    digest = content_sha256(content)
    key = "jobs/job-1/evidence/blob"
    store.put(key, content, sha256=digest)
    (store.root / key).write_bytes(b"tampered")

    with pytest.raises(ObjectStoreError, match="does not match"):
        store.get(key, sha256=digest)


def test_s3_object_store_writes_hash_metadata_and_media_type() -> None:
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    class Client:
        uploaded: dict[str, object] | None = None

        def head_object(self, **_kwargs):
            raise ClientError(
                {"ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )

        def put_object(self, **kwargs):
            self.uploaded = kwargs

    client = Client()
    store = S3ObjectStore.__new__(S3ObjectStore)
    store.bucket = "scientific-state"
    store.prefix = "openscientist"
    store.server_side_encryption = "AES256"
    store.client = client
    content = b"evidence"
    digest = content_sha256(content)

    store.put(
        "jobs/job-1/evidence/blob",
        content,
        sha256=digest,
        content_type="text/plain",
    )

    assert client.uploaded == {
        "Bucket": "scientific-state",
        "Key": "openscientist/jobs/job-1/evidence/blob",
        "Body": content,
        "Metadata": {"sha256": digest},
        "ContentType": "text/plain",
        "ServerSideEncryption": "AES256",
    }


def test_s3_object_store_verifies_downloaded_content() -> None:
    content = b"stored graph"
    digest = content_sha256(content)

    class Client:
        def get_object(self, **_kwargs):
            return {"Body": BytesIO(content)}

    store = S3ObjectStore.__new__(S3ObjectStore)
    store.bucket = "scientific-state"
    store.prefix = ""
    store.server_side_encryption = None
    store.client = Client()

    assert store.get("jobs/job-1/context", sha256=digest) == content
    with pytest.raises(ObjectStoreError, match="does not match"):
        store.get("jobs/job-1/context", sha256="0" * 64)
