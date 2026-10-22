"""
The blob store: files named by the SHA-256 of their bytes.

Content addressing keeps versions immutable for free -- a stored blob can never
be changed under a version that refers to it -- and stores the same bytes once
however often they are published. A blob uploaded for a publish that then loses
its compare-and-set stays behind unreferenced; the retry that follows usually
uploads the same bytes and reuses it.
"""

import hashlib
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK = 1024 * 1024


def path_of(blobs_dir: Path, ref: str) -> Path:
    return blobs_dir / ref[:2] / ref


def exists(blobs_dir: Path, ref: str) -> bool:
    return path_of(blobs_dir, ref).is_file()


async def store(upload: UploadFile, blobs_dir: Path, max_size: int) -> tuple[str, int]:
    """
    Copy `upload` into the store and return its reference and size. Refuses
    (413) one larger than `max_size`, adding none of it to the store.

    The limit protects the store, not the disk: Starlette has already spooled
    the whole multipart body to a temporary file before this runs, and the
    copy here is written on the event loop. Fine for a skill on loopback;
    knowledge bases (Step 6) get a streamed, chunked upload instead.
    """
    digest = hashlib.sha256()
    size = 0
    # In the store's own directory, so the final rename never crosses filesystems.
    fd, tmp_name = tempfile.mkstemp(dir=blobs_dir, prefix=".upload-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Blobs are limited to {max_size} bytes.",
                    )
                digest.update(chunk)
                out.write(chunk)
        ref = digest.hexdigest()
        final = path_of(blobs_dir, ref)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            tmp.unlink()
        else:
            os.replace(tmp, final)
        return ref, size
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
