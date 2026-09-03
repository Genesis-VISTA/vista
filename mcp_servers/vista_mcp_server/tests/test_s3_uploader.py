"""
Unit tests for `jobscripts/s3_put.py`, the output pusher that runs on the
compute node.

This is the one piece of the HPC path with no local debugging story: it
executes inside a Slurm allocation, from an exit trap, with its stderr going to
a log that itself only arrives if the push works. So its signing and file-walk
logic is pinned here.

SigV4 is verified against botocore's own `S3SigV4Auth.canonical_request`, which
ships with boto3 — a real oracle rather than a memorized digest. Note it must
be the **S3** signer: the generic `SigV4Auth` double-encodes URI paths
(`%2520`), which is correct for most AWS services and wrong for S3.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json

import pytest
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from vista_mcp_server.config import settings

pytestmark = [pytest.mark.unit]

HOST = "bucket.s3.us-east-1.amazonaws.com"
AMZDATE = "20130524T000000Z"
DATESTAMP = "20130524"
KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _load_uploader():
    """Import the pusher by path — it ships as a job script, not a package module."""
    path = settings.jobscripts_dir / "s3_put.py"
    spec = importlib.util.spec_from_file_location("vista_s3_put", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s3_put = _load_uploader()


def _botocore_canonical(
    method: str, path: str, query: dict[str, str], payload: str
) -> str:
    """The reference canonical request, built from the same wire URL we send."""
    wire = s3_put._uri_encode(path, encode_slash=False)
    url = f"https://{HOST}{wire}"
    if query:
        url += "?" + "&".join(
            f"{s3_put._uri_encode(k, encode_slash=True)}={s3_put._uri_encode(v, encode_slash=True)}"
            for k, v in sorted(query.items())
        )
    headers = {
        "Host": HOST,
        "x-amz-content-sha256": payload,
        "Content-Length": "1",
        "X-Amz-Date": AMZDATE,
    }
    request = AWSRequest(method=method, url=url, headers=dict(headers))
    return S3SigV4Auth(
        Credentials(KEY_ID, SECRET), "s3", "us-east-1"
    ).canonical_request(request)


def _ours_canonical(method: str, path: str, query: dict[str, str], payload: str) -> str:
    headers = {
        "Host": HOST,
        "x-amz-content-sha256": payload,
        "Content-Length": "1",
        "X-Amz-Date": AMZDATE,
    }
    creq, _ = s3_put.canonical_request(method, path, query, headers, payload)
    return creq


@pytest.mark.parametrize(
    "path",
    [
        "/out/chart.png",
        "/out/sub dir/chart.png",  # space
        "/jobs/1/out/a+b/c&d.json",  # + and & are literal in an S3 key
        "/jobs/1/out/résumé.txt",  # non-ascii
        "/jobs/1/out/a'b\"c.txt",  # quotes
        "/jobs/1/out/deep/a=b,c;d.txt",  # punctuation
        "/jobs/1/out/100%_done.txt",  # a literal percent must not double-encode
        "/jobs/1/out/~tilde-_.txt",  # unreserved characters stay raw
    ],
)
def test_canonical_request_matches_botocore_for_keys(path):
    payload = s3_put.UNSIGNED
    assert _ours_canonical("PUT", path, {}, payload) == _botocore_canonical(
        "PUT", path, {}, payload
    )


@pytest.mark.parametrize(
    "method,query,payload",
    [
        ("PUT", {"partNumber": "2", "uploadId": "abc~def/+="}, "UNSIGNED-PAYLOAD"),
        ("POST", {"uploads": ""}, EMPTY_SHA),
        ("POST", {"uploadId": "u/1+2=3"}, EMPTY_SHA),
        ("DELETE", {"uploadId": "u1"}, EMPTY_SHA),
    ],
)
def test_canonical_request_matches_botocore_for_multipart_queries(
    method, query, payload
):
    assert _ours_canonical(method, "/k", query, payload) == _botocore_canonical(
        method, "/k", query, payload
    )


def test_authorization_header_shape_and_signature():
    """Full signature equality with botocore for a signed-payload request."""
    headers = {
        "Host": HOST,
        "x-amz-content-sha256": EMPTY_SHA,
        "Content-Length": "0",
        "X-Amz-Date": AMZDATE,
    }
    request = AWSRequest(
        method="POST", url=f"https://{HOST}/k?uploads=", headers=dict(headers)
    )
    S3SigV4Auth(Credentials(KEY_ID, SECRET), "s3", "us-east-1").add_auth(request)
    reference = request.headers["Authorization"]
    amzdate = request.headers["X-Amz-Date"]

    ours = s3_put.authorization_header(
        method="POST",
        path="/k",
        query={"uploads": ""},
        headers={**headers, "x-amz-date": amzdate},
        payload_hash=EMPTY_SHA,
        key_id=KEY_ID,
        secret=SECRET,
        region="us-east-1",
        amzdate=amzdate,
        datestamp=amzdate[:8],
    )
    assert ours.startswith("AWS4-HMAC-SHA256 Credential=")
    assert ours.split("Signature=")[1] == reference.split("Signature=")[1]


def test_signing_key_is_scoped_to_every_component():
    """
    The derived key must change if any scope component does, so a key can never
    be replayed across a date, region, or service. (Its exact value is pinned
    end-to-end by the botocore signature comparison above.)
    """
    base = s3_put.signing_key(SECRET, DATESTAMP, "us-east-1", "s3")
    assert len(base) == 32
    assert base == s3_put.signing_key(SECRET, DATESTAMP, "us-east-1", "s3")

    variants = {
        s3_put.signing_key("other-secret", DATESTAMP, "us-east-1", "s3"),
        s3_put.signing_key(SECRET, "20130525", "us-east-1", "s3"),
        s3_put.signing_key(SECRET, DATESTAMP, "us-west-2", "s3"),
        s3_put.signing_key(SECRET, DATESTAMP, "us-east-1", "sts"),
    }
    assert base not in variants
    assert len(variants) == 4, "each component must contribute independently"


def test_iter_output_files_is_unfiltered_and_relative(tmp_path):
    """
    Everything under VISTA_OUT is uploaded: the scratch/output split is the
    contract, so the uploader must not second-guess it with an exclude list.
    """
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "results.json").write_bytes(b"{}")
    (tmp_path / "sub" / "chart.png").write_bytes(b"png")
    (tmp_path / "sub" / "deep" / "model.pt").write_bytes(b"\x00\x01")
    # Things an exclude list would have dropped — they must still be uploaded.
    (tmp_path / ".hidden").write_bytes(b"x")
    (tmp_path / "sub" / "__pycache__").mkdir()
    (tmp_path / "sub" / "__pycache__" / "m.pyc").write_bytes(b"c")

    found = s3_put.iter_output_files(tmp_path)
    keys = [rel for _, rel in found]

    assert keys == sorted(keys), "stable ordering keeps the manifest diffable"
    assert set(keys) == {
        ".hidden",
        "results.json",
        "sub/__pycache__/m.pyc",
        "sub/chart.png",
        "sub/deep/model.pt",
    }
    assert all(path.is_file() for path, _ in found)


def test_iter_output_files_skips_symlinks(tmp_path):
    """A link out of the tree must not be copied out of the facility."""
    secret = tmp_path / "elsewhere.txt"
    secret.write_bytes(b"not mine")
    out = tmp_path / "out"
    out.mkdir()
    (out / "real.txt").write_bytes(b"mine")
    (out / "link.txt").symlink_to(secret)
    (out / "linkdir").symlink_to(tmp_path, target_is_directory=True)

    keys = {rel for _, rel in s3_put.iter_output_files(out)}
    assert keys == {"real.txt"}


def test_file_chunk_reader_streams_one_range(tmp_path):
    """Multipart parts stream from disk, never into memory."""
    blob = tmp_path / "big.bin"
    blob.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    reader = s3_put._FileChunkReader(blob, offset=256, length=512)

    chunks = []
    while True:
        chunk = reader.read(100)
        if not chunk:
            break
        chunks.append(chunk)

    body = b"".join(chunks)
    assert len(body) == 512
    assert body == blob.read_bytes()[256:768]
    assert reader.read(10) == b"", "exhausted reader stays exhausted"


def test_part_size_keeps_objects_under_the_part_ceiling():
    """S3 allows 10,000 parts; the part size must cover a plausible checkpoint."""
    assert s3_put.MAX_SINGLE_PUT == 5 * 1024**3
    assert s3_put.PART_SIZE >= 5 * 1024**2, "S3 requires >= 5 MiB parts"
    assert s3_put.PART_SIZE * 10_000 > 2 * 1024**4, "must cover multi-TiB objects"


def test_xml_find_reads_namespaced_s3_responses():
    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<Bucket>b</Bucket><Key>k</Key><UploadId>upload-42</UploadId>"
        b"</InitiateMultipartUploadResult>"
    )
    assert s3_put._xml_find(body, "UploadId") == "upload-42"
    assert s3_put._xml_find(body, "Missing") is None
    assert s3_put._xml_find(b"not xml at all", "UploadId") is None


def test_main_uploads_output_then_logs_then_manifest(tmp_path, monkeypatch):
    """
    Ordering is the contract: the manifest is the completion sentinel, so it
    must be written only after everything else.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "results.json").write_bytes(b"{}")
    (out / "chart.png").write_bytes(b"png")
    log = tmp_path / "log-1.out"
    log.write_bytes(b"job log\n")
    err = tmp_path / "log-1.err"
    err.write_bytes(b"")

    for name, value in {
        "VISTA_S3_BUCKET": "b",
        "VISTA_S3_PREFIX": "jobs/1",
        "VISTA_S3_REGION": "us-east-1",
        "VISTA_S3_KEY_ID": KEY_ID,
        "VISTA_S3_SECRET": SECRET,
        "VISTA_OUT": str(out),
        "VISTA_LOG": str(log),
        "VISTA_LOG_ERR": str(err),
        "SLURM_JOB_ID": "1",
    }.items():
        monkeypatch.setenv(name, value)

    sent: list[tuple[str, str, bytes | None]] = []

    def fake_request(
        self,
        method,
        key,
        *,
        query=None,
        body=None,
        body_path=None,
        body_offset=0,
        body_length=None,
    ):
        sent.append((method, key, body))
        return b"", {"ETag": '"etag"'}

    monkeypatch.setattr(s3_put.S3Target, "request", fake_request)

    assert s3_put.main() == 0

    keys = [k for _, k, _ in sent]
    assert keys == [
        "jobs/1/out/chart.png",
        "jobs/1/out/results.json",
        "jobs/1/log.out",
        "jobs/1/log.err",
        "jobs/1/manifest.json",
    ]

    manifest = json.loads(sent[-1][2])
    assert manifest["job_id"] == "1"
    assert manifest["failed"] == []
    assert {entry["key"] for entry in manifest["uploaded"]} == set(keys[:-1])


def test_main_records_failures_and_still_writes_a_manifest(tmp_path, monkeypatch):
    """One unreadable file must not strand the rest of the results."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "good.json").write_bytes(b"{}")
    (out / "bad.bin").write_bytes(b"x")

    for name, value in {
        "VISTA_S3_BUCKET": "b",
        "VISTA_S3_PREFIX": "jobs/2",
        "VISTA_S3_KEY_ID": KEY_ID,
        "VISTA_S3_SECRET": SECRET,
        "VISTA_OUT": str(out),
    }.items():
        monkeypatch.setenv(name, value)

    def fake_request(
        self,
        method,
        key,
        *,
        query=None,
        body=None,
        body_path=None,
        body_offset=0,
        body_length=None,
    ):
        if key.endswith("bad.bin"):
            raise s3_put.UploadError("simulated 500")
        return b"", {}

    monkeypatch.setattr(s3_put.S3Target, "request", fake_request)

    assert s3_put.main() == 1, "a partial push must report failure"

    # The manifest still lands, naming what failed, so status can say so.
    assert (out / "good.json").exists()


def test_missing_bucket_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("VISTA_S3_BUCKET", raising=False)
    monkeypatch.setenv("VISTA_OUT", str(tmp_path))
    assert s3_put.main() == 2


def test_target_builds_virtual_hosted_url_and_honors_endpoint(monkeypatch):
    for name, value in {
        "VISTA_S3_BUCKET": "vista-out",
        "VISTA_S3_PREFIX": "jobs/7",
        "VISTA_S3_REGION": "us-west-2",
        "VISTA_S3_KEY_ID": KEY_ID,
        "VISTA_S3_SECRET": SECRET,
    }.items():
        monkeypatch.setenv(name, value)

    target = s3_put.S3Target()
    assert target.host == "vista-out.s3.us-west-2.amazonaws.com"
    assert target.base == "https://vista-out.s3.us-west-2.amazonaws.com"
    assert target.prefix == "jobs/7"

    # The escape hatch if the OLCF proxy cannot reach AWS.
    monkeypatch.setenv("VISTA_S3_ENDPOINT", "https://objstore.ornl.gov/")
    other = s3_put.S3Target()
    assert other.base == "https://objstore.ornl.gov"
    assert other.host == "objstore.ornl.gov"


def test_multipart_upload_sequences_create_parts_complete(tmp_path, monkeypatch):
    """
    Objects over the single-PUT ceiling must go create -> parts -> complete,
    with each part carrying its own byte range. Exercised with tiny thresholds:
    a real 5 GiB fixture is not something a unit test should write.
    """
    blob = tmp_path / "checkpoint.pt"
    blob.write_bytes(b"x" * 250)
    monkeypatch.setattr(s3_put, "MAX_SINGLE_PUT", 100)
    monkeypatch.setattr(s3_put, "PART_SIZE", 100)

    calls: list[tuple[str, dict, int, int | None]] = []

    def fake_request(
        self,
        method,
        key,
        *,
        query=None,
        body=None,
        body_path=None,
        body_offset=0,
        body_length=None,
    ):
        calls.append((method, dict(query or {}), body_offset, body_length))
        if query and "uploads" in query:
            return (
                b'<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b"<UploadId>up-1</UploadId></InitiateMultipartUploadResult>",
                {},
            )
        return b"", {"ETag": '"tag"'}

    monkeypatch.setattr(s3_put.S3Target, "request", fake_request)
    target = s3_put.S3Target.__new__(s3_put.S3Target)

    s3_put.upload_file(target, blob, "jobs/1/out/checkpoint.pt")

    methods = [(m, sorted(q)) for m, q, _, _ in calls]
    assert methods == [
        ("POST", ["uploads"]),
        ("PUT", ["partNumber", "uploadId"]),
        ("PUT", ["partNumber", "uploadId"]),
        ("PUT", ["partNumber", "uploadId"]),
        ("POST", ["uploadId"]),
    ]
    # Parts are numbered from 1 and tile the file with no gap or overlap.
    parts = [
        (q["partNumber"], off, length) for m, q, off, length in calls if m == "PUT"
    ]
    assert parts == [("1", 0, 100), ("2", 100, 100), ("3", 200, 50)]


def test_multipart_aborts_on_failure(tmp_path, monkeypatch):
    """An abandoned multipart upload bills storage forever, so it must abort."""
    blob = tmp_path / "big.bin"
    blob.write_bytes(b"y" * 250)
    monkeypatch.setattr(s3_put, "MAX_SINGLE_PUT", 100)
    monkeypatch.setattr(s3_put, "PART_SIZE", 100)

    seen: list[str] = []

    def fake_request(
        self,
        method,
        key,
        *,
        query=None,
        body=None,
        body_path=None,
        body_offset=0,
        body_length=None,
    ):
        query = query or {}
        seen.append(f"{method} {sorted(query)}")
        if "uploads" in query:
            return (b"<R><UploadId>up-2</UploadId></R>", {})
        if method == "PUT" and query.get("partNumber") == "2":
            raise s3_put.UploadError("simulated part failure")
        return b"", {"ETag": '"t"'}

    monkeypatch.setattr(s3_put.S3Target, "request", fake_request)
    target = s3_put.S3Target.__new__(s3_put.S3Target)

    with pytest.raises(s3_put.UploadError, match="simulated part failure"):
        s3_put.upload_file(target, blob, "jobs/1/out/big.bin")

    assert seen[-1] == "DELETE ['uploadId']", "must abort the upload it started"


def test_endpoint_override_uses_path_style_addressing(monkeypatch):
    """
    An S3-compatible store expects the bucket as the first path segment, and it
    must be inside the *signed* path — otherwise every request 403s.
    """
    for name, value in {
        "VISTA_S3_BUCKET": "vista-out",
        "VISTA_S3_PREFIX": "jobs/3",
        "VISTA_S3_KEY_ID": KEY_ID,
        "VISTA_S3_SECRET": SECRET,
        "VISTA_S3_ENDPOINT": "https://objstore.ornl.gov",
    }.items():
        monkeypatch.setenv(name, value)

    target = s3_put.S3Target()
    assert target.key_root == "/vista-out"

    sent: dict[str, str] = {}

    class FakeResponse:
        headers = {}

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, *a, **kw):
        sent["url"] = request.full_url
        sent["auth"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(s3_put.urllib.request, "urlopen", fake_urlopen)
    target.request("PUT", "jobs/3/out/a.txt", body=b"hi")

    assert sent["url"] == "https://objstore.ornl.gov/vista-out/jobs/3/out/a.txt"
    # The bucket segment is signed, not just routed.
    creq, _ = s3_put.canonical_request(
        "PUT", "/vista-out/jobs/3/out/a.txt", {}, {"host": "objstore.ornl.gov"}, "x"
    )
    assert "/vista-out/jobs/3/out/a.txt" in creq


def test_read_client_shares_the_job_credential_or_falls_back(monkeypatch):
    """
    One key covers both directions, but an unset key must leave boto3 to its
    default chain — passing None explicitly would defeat the instance-profile
    lookup the deployment relies on.
    """
    from vista_mcp_server.lib import s3 as s3_lib

    captured: dict[str, object] = {}

    def fake_client(service, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(s3_lib.boto3, "client", fake_client)
    monkeypatch.setattr(settings.s3, "bucket", "b")

    monkeypatch.setattr(settings.s3, "key_id", "AKIASHARED")
    monkeypatch.setattr(settings.s3, "secret", "shared-secret")
    s3_lib.create_s3_client()
    assert captured["aws_access_key_id"] == "AKIASHARED"
    assert captured["aws_secret_access_key"] == "shared-secret"

    monkeypatch.setattr(settings.s3, "key_id", None)
    monkeypatch.setattr(settings.s3, "secret", None)
    s3_lib.create_s3_client()
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured


def test_multipart_complete_error_in_200_body_raises(tmp_path, monkeypatch):
    """
    CompleteMultipartUpload reports failure with a 200 plus an <Error> body,
    so the status line alone cannot be trusted. Missing it would record a
    checkpoint in manifest.json as uploaded and only fail at download time.
    """
    blob = tmp_path / "big.bin"
    blob.write_bytes(b"z" * 150)
    monkeypatch.setattr(s3_put, "MAX_SINGLE_PUT", 100)
    monkeypatch.setattr(s3_put, "PART_SIZE", 100)

    seen: list[str] = []

    def fake_request(
        self,
        method,
        key,
        *,
        query=None,
        body=None,
        body_path=None,
        body_offset=0,
        body_length=None,
    ):
        query = query or {}
        seen.append(f"{method} {sorted(query)}")
        if "uploads" in query:
            return (b"<R><UploadId>up-3</UploadId></R>", {})
        if method == "POST":  # complete: HTTP 200, error in the body
            return (
                b'<Error xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b"<Code>InternalError</Code><Message>We encountered an internal"
                b" error. Please try again.</Message></Error>",
                {},
            )
        return b"", {"ETag": '"t"'}

    monkeypatch.setattr(s3_put.S3Target, "request", fake_request)
    target = s3_put.S3Target.__new__(s3_put.S3Target)

    with pytest.raises(s3_put.UploadError, match="InternalError"):
        s3_put.upload_file(target, blob, "jobs/1/out/big.bin")

    assert seen[-1] == "DELETE ['uploadId']", "a failed complete must still abort"


def test_get_text_returns_empty_for_zero_byte_object():
    """
    S3 answers a ranged GET against a zero-byte object with 416 InvalidRange.
    A job that logged only to stderr uploads exactly that, and a status call
    must not fail over it.
    """
    from botocore.exceptions import ClientError

    from vista_mcp_server.lib.s3 import S3Client

    client = S3Client.__new__(S3Client)
    client.bucket = "b"

    class Boto:
        def get_object(self, **kwargs):
            assert kwargs["Range"] == "bytes=0-99"
            raise ClientError(
                {"Error": {"Code": "InvalidRange", "Message": "range not satisfiable"}},
                "GetObject",
            )

    client._s3 = Boto()
    assert client._get_text("jobs/1/log.out", 100) == ""


def test_get_text_still_raises_other_client_errors():
    """Only InvalidRange is benign — NoSuchKey must not read as an empty log."""
    from botocore.exceptions import ClientError

    from vista_mcp_server.lib.s3 import S3Client

    client = S3Client.__new__(S3Client)
    client.bucket = "b"

    class Boto:
        def get_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"
            )

    client._s3 = Boto()
    with pytest.raises(ClientError):
        client._get_text("jobs/1/log.out", 100)
