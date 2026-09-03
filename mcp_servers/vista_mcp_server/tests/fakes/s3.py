"""In-memory stand-in for ``lib.s3.S3Client``."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeS3Client:
    """Records S3 reads and serves seeded objects."""

    def __init__(self, *, bucket: str = "fake-bucket"):
        self.bucket = bucket
        self.list_calls: list[str] = []
        self.downloads: list[tuple[str, str]] = []
        self.exists_calls: list[str] = []
        # key -> bytes. Seed this to make a job look like it pushed output.
        self.objects: dict[str, bytes] = {}

    def seed_job_output(self, key_prefix: str, files: dict[str, bytes]) -> None:
        """Seed ``<key_prefix>/out/<rel>`` plus the log and completion manifest."""
        for rel, content in files.items():
            self.objects[f"{key_prefix}/out/{rel}"] = content
        self.objects.setdefault(f"{key_prefix}/log.out", b"line1\nline2\n")
        self.objects.setdefault(f"{key_prefix}/manifest.json", b"{}")

    async def list_objects(self, *, prefix: str) -> list[dict[str, Any]]:
        self.list_calls.append(prefix)
        base = prefix.rstrip("/") + "/"
        return [
            {"key": k, "size": len(v), "path": k[len(base) :]}
            for k, v in sorted(self.objects.items())
            if k.startswith(base)
        ]

    async def object_exists(self, *, key: str) -> bool:
        self.exists_calls.append(key)
        return key in self.objects

    async def get_text(self, *, key: str, max_bytes: int | None = None) -> str:
        if key not in self.objects:
            # Mirrors boto3: a missing object raises rather than returning "".
            raise FileNotFoundError(key)
        body = self.objects[key]
        if max_bytes:
            body = body[:max_bytes]
        return body.decode("utf-8", errors="replace")

    async def download_file(self, *, key: str, local_path: str | Path) -> None:
        if key not in self.objects:
            raise FileNotFoundError(key)
        self.downloads.append((key, str(local_path)))
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])
