#!/usr/bin/env python3
"""
Push an HPC job's output to S3, from the compute node.

This runs *on the cluster*, not in Vista. The Odo/Frontier dispatchers
base64-inline it into the IRI JobSpec and install it as an exit trap, so it
fires on success, on error, and on Slurm's SIGTERM at the time limit — a failed
job is exactly when its log matters most.

Why hand-rolled SigV4 instead of `aws s3 cp` or `curl`: `aws`, `curl`, `wget`
and `tar` appear nowhere in the `hpc_jobs/` catalog (the one `curl` use is
Perlmutter-only), so nothing establishes that any of them exists on an OLCF
compute node. `python3` after `module load cray-python` is the one interpreter
the catalog already relies on everywhere, so this file imports only the
standard library — no boto3, no requests, no pip install at job time.

Contract, all via environment:

    VISTA_S3_BUCKET     destination bucket
    VISTA_S3_PREFIX     key prefix for this job, e.g. "jobs/odo/12345"
    VISTA_S3_REGION     region for SigV4 signing (default us-east-1)
    VISTA_S3_KEY_ID     access key id (shared with Vista's read side)
    VISTA_S3_SECRET     secret for the above
    VISTA_S3_ENDPOINT   optional endpoint override (non-AWS object store)
    VISTA_OUT           directory uploaded verbatim, to <prefix>/out/<relpath>
    VISTA_LOG           optional Slurm stdout path -> <prefix>/log.out
    VISTA_LOG_ERR       optional Slurm stderr path -> <prefix>/log.err

Everything under VISTA_OUT is uploaded with no exclusions: scratch belongs in
$VISTA_SCRATCH, which is not uploaded. `manifest.json` is written **last** and
is the completion sentinel — if a job is terminal but has no manifest, Vista
reports the push as truncated rather than claiming the job produced nothing.

Proxy: OLCF compute nodes have no direct outbound network, and `urllib`
honors the `https_proxy` the dispatcher already exports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ALGORITHM = "AWS4-HMAC-SHA256"

# S3 rejects a single PUT over 5 GiB, so anything larger goes multipart.
# forge-tune writes model checkpoints into $VISTA_OUT and there is no size cap,
# so this path is load-bearing, not theoretical.
MAX_SINGLE_PUT = 5 * 1024**3
PART_SIZE = 256 * 1024**2  # 10,000-part ceiling => ~2.5 TiB max object

# Over HTTPS, S3 accepts UNSIGNED-PAYLOAD in place of the body hash. That
# matters here: hashing a multi-GB checkpoint would mean reading it off Lustre
# twice. Small XML bodies (multipart create/complete) are signed properly.
UNSIGNED = "UNSIGNED-PAYLOAD"

RETRIES = 3
RETRY_BACKOFF_S = 5


class UploadError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# SigV4
# --------------------------------------------------------------------------


def _uri_encode(value: str, *, encode_slash: bool) -> str:
    safe = "-_.~" + ("" if encode_slash else "/")
    return urllib.parse.quote(value, safe=safe)


def _canonical_path(path: str) -> str:
    """ The signed form of a key path — S3 wants single encoding, slashes kept. """
    return _uri_encode(path, encode_slash=False)


def _canonical_query(query: dict[str, str]) -> str:
    """
    The signed form of a query string: sorted, each part fully encoded.

    Shared by `canonical_request` and the URL `S3Target.request` actually
    sends. The two must be byte-identical or the signature will not verify.
    """
    return "&".join(
        f"{_uri_encode(k, encode_slash=True)}={_uri_encode(v, encode_slash=True)}"
        for k, v in sorted(query.items())
    )


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret: str, datestamp: str, region: str, service: str = "s3") -> bytes:
    """Derive the SigV4 signing key (the AWS4 HMAC chain)."""
    k_date = _hmac(f"AWS4{secret}".encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def canonical_request(
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
) -> tuple[str, str]:
    """
    Build the canonical request and its signed-header list.

    Returns `(canonical_request, signed_headers)`. Header names are lowercased
    and sorted; the path is URI-encoded per segment (S3 wants single encoding).
    """
    canonical_uri = _canonical_path(path)
    canonical_query = _canonical_query(query)
    lowered = {k.lower(): v.strip() for k, v in headers.items()}
    canonical_headers = "".join(f"{k}:{lowered[k]}\n" for k in sorted(lowered))
    signed_headers = ";".join(sorted(lowered))
    creq = "\n".join([
        method,
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    return creq, signed_headers


def authorization_header(
    *,
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    key_id: str,
    secret: str,
    region: str,
    amzdate: str,
    datestamp: str,
) -> str:
    """Compute the full `Authorization` header value for one S3 request."""
    creq, signed_headers = canonical_request(method, path, query, headers, payload_hash)
    scope = f"{datestamp}/{region}/s3/aws4_request"
    to_sign = "\n".join([ALGORITHM, amzdate, scope, _sha256_hex(creq.encode("utf-8"))])
    signature = hmac.new(
        signing_key(secret, datestamp, region), to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"{ALGORITHM} Credential={key_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


class S3Target:
    """One bucket, one credential — the minimum needed to PUT."""

    def __init__(self) -> None:
        self.bucket = _require_env("VISTA_S3_BUCKET")
        self.prefix = _require_env("VISTA_S3_PREFIX").strip("/")
        self.region = os.environ.get("VISTA_S3_REGION") or "us-east-1"
        self.key_id = _require_env("VISTA_S3_KEY_ID")
        self.secret = _require_env("VISTA_S3_SECRET")
        endpoint = os.environ.get("VISTA_S3_ENDPOINT")
        if endpoint:
            # Path-style addressing: an S3-compatible store (MinIO, Ceph RGW,
            # an ORNL-side bucket) expects the bucket as the first path
            # segment, not a DNS label. It has to be part of the signed path
            # too, so it lives in `key_root` rather than in `base`.
            self.base = endpoint.rstrip("/")
            self.host = urllib.parse.urlparse(self.base).netloc
            self.key_root = f"/{self.bucket}"
        else:
            # Virtual-hosted style. Bucket names must be DNS-compatible
            # (no dots) or TLS validation fails against the wildcard cert.
            self.host = f"{self.bucket}.s3.{self.region}.amazonaws.com"
            self.base = f"https://{self.host}"
            self.key_root = ""

    def request(
        self,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        body: bytes | None = None,
        body_path: Path | None = None,
        body_offset: int = 0,
        body_length: int | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        """
        Send one signed request, retrying transient failures. Exactly one of
        `body` / `body_path` may be given; `body_path` streams from disk so a
        large part is never held in memory.

        Returns `(response_bytes, response_headers)`.
        """
        query = query or {}
        path = f"{self.key_root}/{key.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, RETRIES + 1):
            # Re-stamp the date each attempt: a retry after a long backoff can
            # otherwise fall outside SigV4's 15-minute skew window.
            now = datetime.now(timezone.utc)
            amzdate = now.strftime("%Y%m%dT%H%M%SZ")
            datestamp = now.strftime("%Y%m%d")

            if body_path is not None:
                length = body_length if body_length is not None else body_path.stat().st_size
                payload_hash = UNSIGNED
                data: object = _FileChunkReader(body_path, body_offset, length)
            else:
                payload = body or b""
                length = len(payload)
                payload_hash = _sha256_hex(payload)
                data = payload

            headers = {
                "Host": self.host,
                "x-amz-date": amzdate,
                "x-amz-content-sha256": payload_hash,
                "Content-Length": str(length),
            }
            headers["Authorization"] = authorization_header(
                method=method,
                path=path,
                query=query,
                headers=headers,
                payload_hash=payload_hash,
                key_id=self.key_id,
                secret=self.secret,
                region=self.region,
                amzdate=amzdate,
                datestamp=datestamp,
            )

            url = self.base + _canonical_path(path)
            if query:
                url += "?" + _canonical_query(query)
            req = urllib.request.Request(url, data=data, method=method)  # type: ignore[arg-type]
            for name, value in headers.items():
                req.add_header(name, value)

            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.read(), dict(resp.headers.items())
            except urllib.error.HTTPError as e:
                detail = e.read()[:2000].decode("utf-8", errors="replace")
                # 4xx other than throttling is a real error: signing, perms, or
                # a bad key. Retrying just delays the failure report.
                if e.code < 500 and e.code != 429:
                    raise UploadError(
                        f"{method} {key} failed: HTTP {e.code} {detail}"
                    ) from e
                last_error = UploadError(f"{method} {key}: HTTP {e.code} {detail}")
            except (urllib.error.URLError, OSError) as e:
                last_error = UploadError(f"{method} {key}: {e}")

            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempt)

        raise UploadError(f"{method} {key} failed after {RETRIES} attempts: {last_error}")


class _FileChunkReader:
    """
    Minimal file-like wrapper exposing one byte range of a file to urllib.

    urllib streams any object with `read()` when Content-Length is set, so this
    lets a 256 MiB multipart part upload without being read into memory.
    """

    def __init__(self, path: Path, offset: int, length: int):
        self._fh = path.open("rb")
        self._fh.seek(offset)
        self._remaining = length

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            self._fh.close()
            return b""
        want = self._remaining if size is None or size < 0 else min(size, self._remaining)
        chunk = self._fh.read(want)
        self._remaining -= len(chunk)
        if not chunk:
            self._fh.close()
        return chunk


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def upload_file(target: S3Target, local: Path, key: str) -> int:
    """Upload one file, choosing single-PUT or multipart by size. Returns bytes sent."""
    size = local.stat().st_size
    if size > MAX_SINGLE_PUT:
        _multipart_upload(target, local, key, size)
    else:
        target.request("PUT", key, body_path=local, body_length=size)
    return size


def _multipart_upload(target: S3Target, local: Path, key: str, size: int) -> None:
    body, _ = target.request("POST", key, query={"uploads": ""}, body=b"")
    upload_id = _xml_find(body, "UploadId")
    if not upload_id:
        raise UploadError(f"multipart create for {key} returned no UploadId")

    try:
        parts: list[tuple[int, str]] = []
        offset = 0
        part_number = 1
        while offset < size:
            length = min(PART_SIZE, size - offset)
            _, resp_headers = target.request(
                "PUT",
                key,
                query={"partNumber": str(part_number), "uploadId": upload_id},
                body_path=local,
                body_offset=offset,
                body_length=length,
            )
            etag = resp_headers.get("ETag") or resp_headers.get("Etag")
            if not etag:
                raise UploadError(f"part {part_number} of {key} returned no ETag")
            parts.append((part_number, etag))
            offset += length
            part_number += 1

        complete = "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{tag}</ETag></Part>"
            for n, tag in parts
        )
        complete_body, _ = target.request(
            "POST",
            key,
            query={"uploadId": upload_id},
            body=f"<CompleteMultipartUpload>{complete}</CompleteMultipartUpload>".encode(),
        )
        # CompleteMultipartUpload is the one call that reports failure with a
        # 200: S3 streams the response while it assembles the object, so an
        # <Error> arrives in the body of an already-successful status line.
        # Without this check a failed checkpoint upload would be recorded in
        # manifest.json as uploaded and only surface as a 404 at download time.
        error_code = _xml_find(complete_body, "Code")
        if error_code:
            raise UploadError(
                f"multipart complete for {key} failed: {error_code} "
                f"({_xml_find(complete_body, 'Message') or 'no message'})"
            )
    except Exception:
        # Leaving an incomplete upload around bills storage forever, so abort
        # before re-raising. Best-effort: the original error is what matters.
        try:
            target.request("DELETE", key, query={"uploadId": upload_id})
        except Exception:
            pass
        raise


def iter_output_files(root: Path) -> list[tuple[Path, str]]:
    """
    Every regular file under `root`, as `(path, relative_key)` sorted by key.

    No exclusions by design: the job contract is that `$VISTA_OUT` is what gets
    saved and `$VISTA_SCRATCH` is where working files go, so filtering here
    would be guesswork about job internals. Symlinks are skipped rather than
    followed — a link into someone else's tree should not be copied out.
    """
    found: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        found.append((path, path.relative_to(root).as_posix()))
    return found


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise UploadError(f"{name} is not set; cannot upload job output")
    return value


def _xml_find(body: bytes, tag: str) -> str | None:
    try:
        root = ET.fromstring(body.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None
    for element in root.iter():
        # S3 responses are namespaced; match on the local tag name.
        if element.tag.rsplit("}", 1)[-1] == tag:
            return (element.text or "").strip() or None
    return None


def main() -> int:
    started = time.time()
    try:
        target = S3Target()
    except UploadError as e:
        print(f"[vista-s3] {e}", file=sys.stderr)
        return 2

    uploaded: list[dict[str, object]] = []
    failed: list[dict[str, str]] = []

    def push(local: Path, key: str) -> None:
        try:
            size = upload_file(target, local, key)
            uploaded.append({"key": key, "size": size})
            print(f"[vista-s3] uploaded {key} ({size} bytes)")
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
            failed.append({"key": key, "error": str(e)})
            print(f"[vista-s3] FAILED {key}: {e}", file=sys.stderr)

    out_dir = os.environ.get("VISTA_OUT")
    if out_dir and Path(out_dir).is_dir():
        for path, rel in iter_output_files(Path(out_dir)):
            push(path, f"{target.prefix}/out/{rel}")
    else:
        print(f"[vista-s3] VISTA_OUT {out_dir!r} missing or empty", file=sys.stderr)

    # Slurm's stdout/stderr live beside the output dir, not inside it, so they
    # are named explicitly rather than picked up by the walk.
    for env_name, key_suffix in (("VISTA_LOG", "log.out"), ("VISTA_LOG_ERR", "log.err")):
        log_path = os.environ.get(env_name)
        if log_path and Path(log_path).is_file():
            push(Path(log_path), f"{target.prefix}/{key_suffix}")

    # Manifest last: its presence is how Vista distinguishes "this job produced
    # nothing" from "the push did not finish".
    manifest = json.dumps({
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "out_dir": out_dir,
        "uploaded": uploaded,
        "failed": failed,
        "elapsed_s": round(time.time() - started, 1),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2).encode()
    try:
        target.request("PUT", f"{target.prefix}/manifest.json", body=manifest)
    except Exception as e:  # noqa: BLE001
        print(f"[vista-s3] FAILED manifest: {e}", file=sys.stderr)
        return 1

    print(f"[vista-s3] {len(uploaded)} uploaded, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
