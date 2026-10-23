"""`POST /blobs/stream`: a blob sent as the raw body, written as it arrives."""

import hashlib

import pytest

pytestmark = pytest.mark.anyio


async def _stream(client, parts: list[bytes]):
    async def body():
        for part in parts:
            yield part

    return await client.post(
        "/blobs/stream",
        content=body(),
        headers={"content-type": "application/octet-stream"},
    )


async def test_a_streamed_body_is_one_blob(client, settings):
    res = await _stream(client, [b"knowledge ", b"base"])
    assert res.status_code == 201, res.text
    ref = res.json()["blob_ref"]
    assert ref == hashlib.sha256(b"knowledge base").hexdigest()
    assert res.json()["size"] == 14
    assert (settings.blobs_dir / ref[:2] / ref).read_bytes() == b"knowledge base"

    # A blob like any other: a version can name it.
    item = await client.post(
        "/items",
        json={
            "kind": "knowledge-base",
            "name": "salt",
            "manifest": {},
            "blob_ref": ref,
        },
    )
    assert item.status_code == 201, item.text
    blob = await client.get(f"/items/{item.json()['id']}/versions/1/blob")
    assert blob.content == b"knowledge base"


async def test_a_streamed_body_over_the_limit_is_413_and_not_kept(client, settings):
    res = await _stream(client, [b"x" * 1000, b"x" * 25])
    assert res.status_code == 413
    assert [p for p in settings.blobs_dir.rglob("*") if p.is_file()] == []


async def test_streaming_needs_an_install_id(anon):
    res = await anon.post("/blobs/stream", content=b"a")
    assert res.status_code == 401
