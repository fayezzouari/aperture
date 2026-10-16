"""R1.1 / R1.8 - a multipart upload lands on the version it was opened for, exactly once."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app import storage
from app.models import Model, ModelVersion, UploadSession

ONNX = {"kind": "onnx", "usability": "servable"}


class FakeStore:
    """Object storage in a dict: enough of app.storage for the upload routes."""

    def __init__(self):
        self.parts: dict[str, list[bytes]] = {}
        self.objects: dict[str, bytes] = {}
        self.completed: list[str] = []
        self.lie_about_size: int | None = None

    async def create_multipart(self, bucket, key):
        self.parts[key] = []
        return f"mp-{key}"

    async def upload_part(self, bucket, key, upload_id, part_number, data):
        self.parts[key].append(data)
        return f"etag-{part_number}"

    async def complete_multipart(self, bucket, key, upload_id, parts):
        if key not in self.parts:
            raise RuntimeError("NoSuchUpload")  # what S3 says to a second complete
        self.objects[key] = b"".join(self.parts.pop(key))
        self.completed.append(key)

    async def verify_and_inspect(self, bucket, key):
        data = self.objects[key]
        size = self.lie_about_size if self.lie_about_size is not None else len(data)
        return "0" * 64, size, ONNX

    async def delete_object(self, bucket, key):
        self.objects.pop(key, None)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in ("create_multipart", "upload_part", "complete_multipart",
                 "verify_and_inspect", "delete_object"):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    return fake


@pytest.fixture
async def model(db):
    row = Model(slug="cells", name="Cells", task="detect")
    db.add(row)
    await db.flush()
    db.add_all([ModelVersion(model_id=row.id, version=1, status="draft"),
                ModelVersion(model_id=row.id, version=2, status="draft")])
    await db.commit()
    return row


async def _open(client, headers, version: int, body: bytes = b"weights") -> str:
    res = await client.post(f"/v1/models/cells/versions/{version}/uploads", headers=headers,
                            json={"filename": "model.onnx", "total_bytes": len(body)})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _send(client, headers, upload_id: str, body: bytes = b"weights"):
    res = await client.put(f"/v1/uploads/{upload_id}/parts/1", headers=headers,
                           files={"chunk": ("part", body)})
    assert res.status_code == 200, res.text


async def _version(db, model_id: str, number: int) -> ModelVersion:
    # populate_existing: the API wrote these rows through its own session
    return (await db.execute(
        sa.select(ModelVersion)
        .where(ModelVersion.model_id == model_id, ModelVersion.version == number)
        .execution_options(populate_existing=True))).scalar_one()


async def _upload(db, upload_id: str) -> UploadSession:
    return await db.get(UploadSession, upload_id, populate_existing=True)


async def test_interleaved_uploads_land_on_their_own_versions(client, admin, db, model, store):
    # v1's upload is opened first and finished last: the old rule gave its
    # weights to v2, the newest draft at completion time
    first = await _open(client, admin.headers, 1, b"one")
    second = await _open(client, admin.headers, 2, b"two!")
    await _send(client, admin.headers, second, b"two!")
    await _send(client, admin.headers, first, b"one")

    done2 = await client.post(f"/v1/uploads/{second}/complete", headers=admin.headers)
    done1 = await client.post(f"/v1/uploads/{first}/complete", headers=admin.headers)
    assert done2.status_code == 200, done2.text
    assert done1.status_code == 200, done1.text
    assert done1.json()["version"] == 1 and done2.json()["version"] == 2

    v1, v2 = await _version(db, model.id, 1), await _version(db, model.id, 2)
    assert v1.weights_bytes == 3 and "/v1/" in v1.weights_key
    assert v2.weights_bytes == 4 and "/v2/" in v2.weights_key


async def test_session_records_its_version(client, admin, db, model, store):
    upload_id = await _open(client, admin.headers, 1)
    row = await _upload(db, upload_id)
    target = await _version(db, model.id, 1)
    assert row.version_id == target.id


async def test_completing_twice_is_a_conflict_not_a_500(client, admin, model, store):
    upload_id = await _open(client, admin.headers, 1)
    await _send(client, admin.headers, upload_id)
    assert (await client.post(f"/v1/uploads/{upload_id}/complete",
                              headers=admin.headers)).status_code == 200

    again = await client.post(f"/v1/uploads/{upload_id}/complete", headers=admin.headers)
    assert again.status_code == 409
    assert "already complete" in again.json()["error"]["message"]
    assert store.completed == [store.completed[0]]  # S3 was not asked a second time


async def test_size_mismatch_discards_the_object_and_closes_the_session(
        client, admin, db, model, store):
    upload_id = await _open(client, admin.headers, 1)
    await _send(client, admin.headers, upload_id)
    store.lie_about_size = 3  # S3 holds fewer bytes than the session declared

    res = await client.post(f"/v1/uploads/{upload_id}/complete", headers=admin.headers)
    assert res.status_code == 422
    assert res.json()["error"]["details"] == {"received_bytes": 3, "declared_bytes": 7}
    assert store.objects == {}  # the partial object is gone

    assert (await _upload(db, upload_id)).status == "failed"
    retry = await client.post(f"/v1/uploads/{upload_id}/complete", headers=admin.headers)
    assert retry.status_code == 409
    assert (await _version(db, model.id, 1)).weights_key is None


async def test_version_that_moved_on_refuses_the_weights(client, admin, db, model, store):
    upload_id = await _open(client, admin.headers, 1)
    await _send(client, admin.headers, upload_id)
    v1 = await _version(db, model.id, 1)
    v1.status = "ready"
    await db.commit()

    res = await client.post(f"/v1/uploads/{upload_id}/complete", headers=admin.headers)
    assert res.status_code == 409
    assert "no longer takes weights" in res.json()["error"]["message"]
    assert store.completed == []


async def test_legacy_session_without_a_version_falls_back_to_the_newest_draft(
        client, admin, db, model, store):
    upload_id = await _open(client, admin.headers, 1)
    await _send(client, admin.headers, upload_id)
    row = await _upload(db, upload_id)
    row.version_id = None  # what a session opened before migration 0004 looks like
    await db.commit()

    res = await client.post(f"/v1/uploads/{upload_id}/complete", headers=admin.headers)
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 2
