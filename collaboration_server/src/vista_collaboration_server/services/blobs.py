"""
The blob store: files named by the SHA-256 of their bytes.

Content addressing keeps versions immutable for free -- a stored blob can never
be changed under a version that refers to it -- and stores the same bytes once
however often they are published. A blob uploaded for a publish that then loses
its compare-and-set stays behind unreferenced; the retry that follows usually
uploads the same bytes and reuses it.
"""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK = 1024 * 1024


def path_of(blobs_dir: Path, ref: str) -> Path:
    return blobs_dir / ref[:2] / ref


def exists(blobs_dir: Path, ref: str) -> bool:
    return path_of(blobs_dir, ref).is_file()


async def store(upload: UploadFile, blobs_dir: Path, max_size: int) -> tuple[str, int]:
    """
    Copy a multipart `upload` into the store (`store_stream`). Starlette has
    already spooled the whole multipart body to a temporary file before this
    runs, so the limit protects the store, not the disk: fine for a skill, and
    a knowledge base is sent to `POST /blobs/stream` instead.
    """

    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await upload.read(CHUNK):
            yield chunk

    return await store_stream(chunks(), blobs_dir, max_size)


async def store_stream(
    chunks: AsyncIterator[bytes], blobs_dir: Path, max_size: int
) -> tuple[str, int]:
    """
    Write `chunks` into the store as they arrive, hashing them on the way, and
    return the blob's reference and size. Refuses (413) one larger than
    `max_size`, adding none of it to the store; a body that ends early (the
    client gave up) leaves nothing either. Writes are gathered to `CHUNK`
    bytes and made in a thread, so a large body never blocks the event loop.
    """
    digest = hashlib.sha256()
    size = 0
    # In the store's own directory, so the final rename never crosses filesystems.
    fd, tmp_name = tempfile.mkstemp(dir=blobs_dir, prefix=".upload-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            pending = bytearray()
            async for chunk in chunks:
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Blobs are limited to {max_size} bytes.",
                    )
                digest.update(chunk)
                pending += chunk
                if len(pending) >= CHUNK:
                    await asyncio.to_thread(out.write, bytes(pending))
                    pending.clear()
            if pending:
                await asyncio.to_thread(out.write, bytes(pending))
        return _place(tmp, blobs_dir, digest.hexdigest()), size
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _place(tmp: Path, blobs_dir: Path, ref: str) -> str:
    """Move the file `tmp`, whose SHA-256 is `ref`, into the store; or drop it if there."""
    final = path_of(blobs_dir, ref)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        tmp.unlink()
    else:
        os.replace(tmp, final)
    return ref
