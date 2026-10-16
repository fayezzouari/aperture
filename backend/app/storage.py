"""S3/MinIO object storage: weights, captured payloads, overlays, report artifacts.

boto3 is synchronous, so every call is pushed to a worker thread. Multipart
upload is exposed directly because R1.1 needs resumable chunks for multi-GB
weights, and a presigned URL is how a runtime pulls weights without proxying
gigabytes through the API.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.settings import settings

log = logging.getLogger("aperture.storage")


@lru_cache
def _client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=300,
        ),
    )


@dataclass(slots=True)
class ObjectInfo:
    key: str
    bucket: str
    size: int
    etag: str
    sha256: str | None = None


def _sse_args() -> dict[str, Any]:
    return {"ServerSideEncryption": settings.s3_sse} if settings.s3_sse else {}


async def _run(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


async def ensure_buckets() -> None:
    client = _client()

    def _ensure(bucket: str) -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError:
            client.create_bucket(Bucket=bucket)
            log.info("created bucket", extra={"bucket": bucket})

    for bucket in (settings.s3_bucket_weights, settings.s3_bucket_captures,
                   settings.s3_bucket_reports):
        await _run(_ensure, bucket)


async def put_bytes(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
                    ) -> ObjectInfo:
    digest = hashlib.sha256(data).hexdigest()
    res = await _run(
        _client().put_object, Bucket=bucket, Key=key, Body=data, ContentType=content_type,
        Metadata={"sha256": digest}, **_sse_args(),
    )
    return ObjectInfo(key=key, bucket=bucket, size=len(data),
                      etag=res.get("ETag", "").strip('"'), sha256=digest)


async def get_bytes(bucket: str, key: str) -> bytes:
    res = await _run(_client().get_object, Bucket=bucket, Key=key)
    return await _run(res["Body"].read)


async def delete_object(bucket: str, key: str) -> None:
    await _run(_client().delete_object, Bucket=bucket, Key=key)


async def delete_prefix(bucket: str, prefix: str) -> int:
    """Hard delete every object under a prefix (R7.6). Returns how many went.

    Batched DeleteObjects is the fast path, but S3-compatible stores disagree
    about which integrity header it must carry: MinIO wants Content-MD5, while
    current botocore sends a CRC32 checksum instead. Rather than pin a
    combination that happens to agree today, fall back to per-object deletes -
    slower, but it works against every implementation.
    """
    client = _client()

    def _purge() -> int:
        removed = 0
        batched = True
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not keys:
                continue
            if batched:
                try:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                    removed += len(keys)
                    continue
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code not in ("MissingContentMD5", "NotImplemented", "InvalidRequest"):
                        raise
                    log.info("batched delete refused by this object store, deleting one by one",
                             extra={"code": code, "bucket": bucket})
                    batched = False
            for item in keys:
                try:
                    client.delete_object(Bucket=bucket, Key=item["Key"])
                    removed += 1
                except ClientError:
                    log.warning("could not delete object", extra={"key": item["Key"]})
        return removed

    return await _run(_purge)


async def head(bucket: str, key: str) -> dict | None:
    try:
        return await _run(_client().head_object, Bucket=bucket, Key=key)
    except ClientError:
        return None


async def presign_get(bucket: str, key: str, expires: int = 3600, *, public: bool = False) -> str:
    """`public=True` signs against the browser-reachable endpoint (overlays in the UI)."""
    client = _client(settings.s3_public_endpoint if public else settings.s3_endpoint)
    return await _run(
        client.generate_presigned_url, "get_object",
        Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires,
    )


# ------------------------------------------------------------------ multipart


async def create_multipart(bucket: str, key: str) -> str:
    res = await _run(_client().create_multipart_upload, Bucket=bucket, Key=key, **_sse_args())
    return res["UploadId"]


async def upload_part(bucket: str, key: str, upload_id: str, part_number: int, data: bytes) -> str:
    res = await _run(
        _client().upload_part, Bucket=bucket, Key=key, UploadId=upload_id,
        PartNumber=part_number, Body=data,
    )
    return res["ETag"].strip('"')


async def complete_multipart(bucket: str, key: str, upload_id: str,
                             parts: list[dict]) -> ObjectInfo:
    ordered = sorted(parts, key=lambda p: p["part"])
    res = await _run(
        _client().complete_multipart_upload, Bucket=bucket, Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [{"PartNumber": p["part"], "ETag": p["etag"]} for p in ordered]},
    )
    meta = await head(bucket, key)
    return ObjectInfo(key=key, bucket=bucket, size=int(meta["ContentLength"]) if meta else 0,
                      etag=res.get("ETag", "").strip('"'))


async def abort_multipart(bucket: str, key: str, upload_id: str) -> None:
    try:
        await _run(_client().abort_multipart_upload, Bucket=bucket, Key=key, UploadId=upload_id)
    except ClientError:
        log.warning("could not abort multipart upload", extra={"key": key})


async def checksum_object(bucket: str, key: str, chunk: int = 8 * 1024 * 1024) -> tuple[str, int]:
    """R1.8 - stream the stored object back and verify its SHA-256 end to end."""

    def _sum() -> tuple[str, int]:
        body = _client().get_object(Bucket=bucket, Key=key)["Body"]
        digest = hashlib.sha256()
        size = 0
        while True:
            block = body.read(chunk)
            if not block:
                break
            digest.update(block)
            size += len(block)
        return digest.hexdigest(), size

    return await _run(_sum)


async def download_to_path(bucket: str, key: str, path: str) -> None:
    await _run(_client().download_file, bucket, key, path)


async def verify_and_inspect(bucket: str, key: str) -> tuple[str, int, dict]:
    """Re-read the stored object to verify it (R1.8) and to see what it is.

    One download does both: the checksum proves the bytes survived the trip, and
    the local file lets the artifact inspector look inside a zip, which needs
    random access and therefore cannot be done from a stream.
    """
    import hashlib
    import tempfile

    from aperture_core.artifacts import inspect_artifact

    def _work() -> tuple[str, int, dict]:
        with tempfile.NamedTemporaryFile(prefix="aperture-artifact-",
                                         suffix=_suffix(key)) as handle:
            _client().download_fileobj(bucket, key, handle)
            handle.flush()
            handle.seek(0)
            digest = hashlib.sha256()
            size = 0
            while block := handle.read(8 * 1024 * 1024):
                digest.update(block)
                size += len(block)
            info = inspect_artifact(handle.name, filename=key.rsplit("/", 1)[-1])
            return digest.hexdigest(), size, info.as_dict()

    return await _run(_work)


def _suffix(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    return f".{name.rsplit('.', 1)[-1]}" if "." in name else ""


def weights_key(model_slug: str, version: int, filename: str) -> str:
    return f"{model_slug}/v{version}/{filename}"


def capture_key(model_slug: str, request_id: str, index: int, ext: str) -> str:
    return f"{model_slug}/{request_id}/{index}.{ext}"
