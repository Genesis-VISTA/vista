"""The Globus HTTPS interface, as `lib/globus.py` talks to it.

Two things are being pinned here, and both come from probing the live OLCF
collections rather than from the protocol:

* **An expired session and a missing file must never be confused.** That
  confusion is the original bug -- a job that ran, succeeded, and then reported
  "no output files yet" because the credential had lapsed in the queue. The
  wire distinguishes them by status code and content type, so the client has to
  as well, with a type the caller can branch on.
* **`HEAD` is the only source of a file's size.** A plain `GET` returns no
  `Content-Length` and a ranged `206` reports its total as `*`, so any code
  that learned a size some other way is reading something that is not there.

No network: `httpx` is driven through a mock transport, and the two Globus
authorizers are stubbed, since what they mint is not what is under test.
"""

from __future__ import annotations

import json
from functools import partial

import globus_sdk
import httpx
import pytest

from vista_mcp_server.lib import globus as globus_lib
from vista_mcp_server.lib.globus import (
    GlobusClient,
    GlobusFileNotFound,
    GlobusSessionExpired,
)
from vista_mcp_server.lib.types import GlobusTokens

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

COLLECTION = "36d521b3-c182-4071-b7d5-91db5d380d42"
SERVER = "https://m-36da98.abb646.36fe.data.globus.org"

GARE_401 = {
    "code": "InvalidToken",
    "authorization_parameters": {
        "session_message": (
            "You must authenticate or link with an identity from one of these "
            "domains (sso.ccs.ornl.gov, clients.auth.globus.org) which has been "
            "granted access to this collection."
        ),
        "session_required_single_domain": ["sso.ccs.ornl.gov"],
    },
}


class _StubAuthorizer:
    def __init__(self, *, refresh_token, auth_client):
        self.refresh_token = refresh_token

    def get_authorization_header(self) -> str:
        return "Bearer fake"


@pytest.fixture
def client(monkeypatch):
    """A `GlobusClient` wired to a handler the test supplies.

    `requests` collects every request that reached the transport, so a test can
    assert on the exact `Range` header sent -- which is the whole of the tail
    design.
    """

    def build(handler):
        recorded: list[httpx.Request] = []

        async def transport_handler(request: httpx.Request) -> httpx.Response:
            recorded.append(request)
            return handler(request)

        real = httpx.AsyncClient
        monkeypatch.setattr(
            globus_lib.httpx,
            "AsyncClient",
            partial(real, transport=httpx.MockTransport(transport_handler)),
        )
        # `RefreshTokenAuthorizer` mints an access token in its constructor, so
        # building one here would be a real call to Globus Auth.
        monkeypatch.setattr(
            globus_lib.globus_sdk, "RefreshTokenAuthorizer", _StubAuthorizer
        )

        instance = GlobusClient(
            tokens=GlobusTokens(transfer="t", https="h"),
            cluster="frontier",
            client_id="00000000-0000-0000-0000-000000000000",
        )
        # Discovery and token minting are Globus's, not this module's; both are
        # covered by their own surfaces and neither is what these tests assert.
        instance._https_servers[COLLECTION] = SERVER

        async def _header():
            return {"Authorization": "Bearer fake"}

        monkeypatch.setattr(instance, "_auth_header", _header)
        return instance, recorded

    return build


def ok(content: bytes = b"hello", **headers) -> httpx.Response:
    return httpx.Response(200, content=content, headers=headers)


class TestTellingFailuresApart:
    async def test_a_lapsed_session_says_which_connection_to_redo(self, client):
        """The 3-day High Assurance timeout is an expected outcome of a long
        Frontier queue wait, not an exceptional one, so it has to arrive naming
        the enclave and the remedy."""
        c, _ = client(
            lambda r: httpx.Response(
                401,
                json=GARE_401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        )

        with pytest.raises(GlobusSessionExpired) as refusal:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")

        message = str(refusal.value)
        assert "Frontier" in message
        assert "settings" in message.lower()

    async def test_globus_own_words_are_passed_through(self, client):
        """The GARE `session_message` names the identity domains the collection
        accepts. That is the actual remedy, and it is not something VISTA could
        state correctly on its own."""
        c, _ = client(lambda r: httpx.Response(401, json=GARE_401))

        with pytest.raises(GlobusSessionExpired) as refusal:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")

        assert "sso.ccs.ornl.gov" in str(refusal.value)

    async def test_a_401_that_is_not_gare_still_names_the_remedy(self, client):
        """Globus is not obliged to send a body VISTA can parse, and a refusal
        that degraded into a traceback would be worse than one that says less."""
        c, _ = client(lambda r: httpx.Response(401, text="nope"))

        with pytest.raises(GlobusSessionExpired) as refusal:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")
        assert "Reconnect Globus" in str(refusal.value)

    async def test_a_missing_file_is_its_own_type(self, client):
        """ "No outputs yet" is the correct reading of this and only this. The
        collections answer it `404 text/plain`, against `401 application/json`
        for a credential, so nothing has to read a message to tell them apart."""
        c, _ = client(lambda r: httpx.Response(404, text="not found"))

        with pytest.raises(GlobusFileNotFound):
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/missing")

    async def test_anything_else_is_reported_verbatim(self, client):
        c, _ = client(lambda r: httpx.Response(503, text="collection is down"))

        with pytest.raises(Exception) as failure:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")
        assert "503" in str(failure.value)
        assert not isinstance(failure.value, (GlobusSessionExpired, GlobusFileNotFound))


class TestSizeAndRanges:
    async def test_stat_reads_the_size_from_head(self, client):
        c, recorded = client(lambda r: ok(b"", **{"Content-Length": "4096"}))

        assert (
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")
            == 4096
        )
        assert recorded[0].method == "HEAD"
        assert str(recorded[0].url) == f"{SERVER}/lustre/log.out"

    async def test_a_head_without_a_size_is_refused_rather_than_guessed(self, client):
        """Every offset the tail computes comes from here. A silent zero would
        make each poll re-read the log from the start, forever."""
        c, _ = client(lambda r: httpx.Response(200, content=b"", headers={}))

        with pytest.raises(Exception) as failure:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")
        assert "size" in str(failure.value)

    async def test_a_range_is_always_explicit_start_end(self, client):
        """Suffix ranges (`bytes=-N`) answer 416 on these collections: the
        server streams from the filesystem without seeking to the end, so it
        cannot know where the end is. Every range this client sends has both
        bounds."""
        c, recorded = client(lambda r: httpx.Response(206, content=b"tail"))

        got = await c.read_range(
            collection_id=COLLECTION, remote_path="/lustre/log.out", start=100, end=199
        )

        assert got == b"tail"
        assert recorded[0].headers["Range"] == "bytes=100-199"

    async def test_a_range_the_server_ignored_is_sliced_locally(self, client):
        """A `200` means the whole file came back — a proxy in the way, most
        likely. Handing it to a caller that appends at `start` would put the log
        in the file twice, and every later poll would then see a remote file
        shorter than its local copy and start over. Forever."""
        whole = b"0123456789"
        c, _ = client(lambda r: httpx.Response(200, content=whole))

        got = await c.read_range(
            collection_id=COLLECTION, remote_path="/lustre/log.out", start=4, end=6
        )

        assert got == b"456"

    async def test_an_empty_range_asks_for_nothing(self, client):
        """A poll that finds no new bytes should not spend a request to be told
        so, and `bytes=100-99` is not a range any server will accept."""
        c, recorded = client(lambda r: pytest.fail("should not have been called"))

        assert (
            await c.read_range(
                collection_id=COLLECTION,
                remote_path="/lustre/log.out",
                start=100,
                end=99,
            )
            == b""
        )
        assert recorded == []


class TestWhereTheMessageComesFrom:
    async def test_a_401_on_head_still_carries_globus_own_words(self, client):
        """A `HEAD` has no body by definition, and the body is where the GARE
        `session_message` lives. `stat` is the first HTTPS request of every
        status poll, so without a second request the message would be lost in
        very nearly every real expiry."""

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(401)
            return httpx.Response(401, json=GARE_401)

        c, recorded = client(handler)

        with pytest.raises(GlobusSessionExpired) as refusal:
            await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")

        assert "sso.ccs.ornl.gov" in str(refusal.value)
        # And the second request costs one byte, not the file.
        assert recorded[1].headers["Range"] == "bytes=0-0"

    async def test_a_successful_head_makes_only_one_request(self, client):
        """The extra fetch is on the failure path and must stay there."""
        c, recorded = client(lambda r: ok(b"", **{"Content-Length": "10"}))

        await c.stat(collection_id=COLLECTION, remote_path="/lustre/log.out")

        assert len(recorded) == 1


class TestACredentialGlobusWontExchange:
    """A refresh token can fail before any request is made.

    Revoked consent, a withdrawn identity, or a token minted before the scope
    set changed all fail when the authorizer mints its first access token --
    as an `AuthAPIError`, which is neither the HTTPS 401 nor the
    `TransferAPIError` the two mappings above look at. Unmapped it surfaces as
    "unable to fetch logs", which is the credential-reported-as-data-problem
    this module exists to stop.
    """

    def auth_error(self):
        import requests

        response = requests.Response()
        response.status_code = 400
        response._content = b'{"error":"invalid_grant"}'
        response.headers["Content-Type"] = "application/json"
        response.reason = "Bad Request"
        response.request = requests.Request(
            "POST", "https://auth.globus.org/v2/oauth2/token"
        ).prepare()
        return globus_sdk.AuthAPIError(response)

    async def test_a_dead_refresh_token_reads_as_an_expired_session(
        self, client, monkeypatch
    ):
        c, _ = client(lambda r: pytest.fail("no request should be attempted"))
        error = self.auth_error()

        def refuse(**kwargs):
            raise error

        monkeypatch.setattr(globus_lib.globus_sdk, "RefreshTokenAuthorizer", refuse)

        # `_https_auth`, not a request: the fixture stubs `_auth_header` so the
        # other tests need no real token, and it is the authorizer underneath
        # that is being checked here.
        with pytest.raises(GlobusSessionExpired) as refusal:
            c._https_auth()
        assert "Frontier" in str(refusal.value)

    async def test_the_transfer_side_says_the_same_thing(self, client, monkeypatch):
        c, _ = client(lambda r: pytest.fail("no request should be attempted"))
        error = self.auth_error()

        def refuse(**kwargs):
            raise error

        monkeypatch.setattr(globus_lib.globus_sdk, "RefreshTokenAuthorizer", refuse)

        with pytest.raises(GlobusSessionExpired):
            await c.operation_ls(endpoint=COLLECTION, path="/lustre/out")


def sized(content: bytes):
    """A handler serving `content`, answering `HEAD` with its length.

    What a whole-file fetch does twice: `HEAD` for the size, then the range
    that size implies.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(content))})
        return httpx.Response(
            206 if "Range" in request.headers else 200, content=content
        )

    return handler


class TestMovingWholeFiles:
    async def test_a_download_streams_to_the_named_path(self, client, tmp_path):
        c, recorded = client(sized(b"checkpoint bytes"))
        target = tmp_path / "nested" / "model.pt"

        await c.download_file(
            collection_id=COLLECTION, remote_path="/lustre/model.pt", local_path=target
        )

        assert target.read_bytes() == b"checkpoint bytes"
        # The size first, then the whole file as one explicit range -- which is
        # what gives the answer a length to be checked against.
        assert [r.method for r in recorded] == ["HEAD", "GET"]
        assert recorded[1].headers["Range"] == "bytes=0-15"

    async def test_a_download_that_stopped_early_is_not_kept(self, client, tmp_path):
        """The failure a plain `GET` cannot see. With no `Content-Length` a body
        that stops early ends the stream cleanly, so without the size from
        `HEAD` the fragment is renamed into place looking complete -- and
        `_get_olcf_job_outputs` never fetches it again."""

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(200, headers={"Content-Length": "4096"})
            return httpx.Response(206, content=b"first page only")

        c, _ = client(handler)
        target = tmp_path / "model.pt"

        with pytest.raises(Exception) as failure:
            await c.download_file(
                collection_id=COLLECTION,
                remote_path="/lustre/model.pt",
                local_path=target,
            )

        assert "incomplete" in str(failure.value)
        assert "4096" in str(failure.value)
        # Neither the fragment nor a `.part` of it survives to be mistaken for
        # the file on the next poll.
        assert list(tmp_path.iterdir()) == []

    async def test_an_empty_file_is_fetched_without_a_range(self, client, tmp_path):
        """`bytes=0--1` is not a range, and a file with nothing in it is a real
        thing to download -- a job that wrote no stderr, most often."""
        c, recorded = client(sized(b""))
        target = tmp_path / "stderr.txt"

        await c.download_file(
            collection_id=COLLECTION,
            remote_path="/lustre/stderr.txt",
            local_path=target,
        )

        assert target.read_bytes() == b""
        assert "Range" not in recorded[1].headers

    async def test_a_file_longer_than_its_head_said_is_still_kept(
        self, client, tmp_path
    ):
        """A `200` means the range was ignored and the whole file came back, and
        a file still being written is by then longer than `HEAD` reported. More
        than was asked for is the file; only less than that is a fragment."""

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(200, headers={"Content-Length": "4"})
            return httpx.Response(200, content=b"grew past the stat")

        c, _ = client(handler)
        target = tmp_path / "log.out"

        await c.download_file(
            collection_id=COLLECTION, remote_path="/lustre/log.out", local_path=target
        )

        assert target.read_bytes() == b"grew past the stat"

    async def test_an_interrupted_download_leaves_no_file_behind(
        self, client, tmp_path
    ):
        """A half file would look complete to the caller's
        already-downloaded check and never be fetched again."""

        def explode(request):
            if request.method == "HEAD":
                return httpx.Response(200, headers={"Content-Length": "16"})
            raise httpx.ReadError("connection dropped")

        c, _ = client(explode)
        target = tmp_path / "model.pt"

        with pytest.raises(httpx.ReadError):
            await c.download_file(
                collection_id=COLLECTION,
                remote_path="/lustre/model.pt",
                local_path=target,
            )

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    async def test_a_download_of_a_missing_file_is_not_an_empty_file(
        self, client, tmp_path
    ):
        c, _ = client(lambda r: httpx.Response(404, text="not found"))
        target = tmp_path / "model.pt"

        with pytest.raises(GlobusFileNotFound):
            await c.download_file(
                collection_id=COLLECTION,
                remote_path="/lustre/model.pt",
                local_path=target,
            )
        assert not target.exists()

    async def test_an_upload_puts_the_files_bytes(self, client, tmp_path):
        c, recorded = client(sized(b"print('hi')\n"))
        source = tmp_path / "run.py"
        source.write_bytes(b"print('hi')\n")

        await c.upload_file(
            collection_id=COLLECTION,
            local_path=source,
            remote_path="/lustre/src/run.py",
        )

        assert recorded[0].method == "PUT"
        assert str(recorded[0].url) == f"{SERVER}/lustre/src/run.py"
        assert recorded[0].content == b"print('hi')\n"
        # And the size that landed is read back: a 2xx says the request
        # finished, not that the whole body was stored.
        assert recorded[1].method == "HEAD"

    async def test_an_upload_that_did_not_land_whole_is_refused(self, client, tmp_path):
        """A source file that lands short is a job that fails on the cluster
        with a syntax error pointing nowhere near the cause."""

        def handler(request):
            if request.method == "HEAD":
                return httpx.Response(200, headers={"Content-Length": "4"})
            return ok(b"")

        c, _ = client(handler)
        source = tmp_path / "run.py"
        source.write_bytes(b"print('hi')\n")

        with pytest.raises(Exception) as failure:
            await c.upload_file(
                collection_id=COLLECTION,
                local_path=source,
                remote_path="/lustre/src/run.py",
            )

        assert "run.py" in str(failure.value)
        assert "4 of 12 bytes" in str(failure.value)

    async def test_a_path_with_spaces_is_escaped_but_still_a_path(self, client):
        """Separators have to survive; everything else has to be escaped, or
        the request line is invalid and Globus answers something unrelated to
        the file."""
        c, recorded = client(lambda r: ok(b"", **{"Content-Length": "1"}))

        await c.stat(collection_id=COLLECTION, remote_path="/lustre/my runs/log 1.out")

        assert str(recorded[0].url) == f"{SERVER}/lustre/my%20runs/log%201.out"


class TestGareParsing:
    def test_a_body_that_is_not_json_yields_no_message(self):
        assert globus_lib._gare_message("<html>502</html>") is None

    def test_the_session_message_is_preferred(self):
        assert "sso.ccs.ornl.gov" in globus_lib._gare_message(json.dumps(GARE_401))

    def test_a_plain_detail_is_used_when_there_is_no_gare(self):
        assert globus_lib._gare_message(json.dumps({"detail": "token expired"})) == (
            "token expired"
        )


class TestTheTransferSide:
    """The other half of D5.

    Transfer reports a lapsed High Assurance session as `401
    AuthenticationFailed`. It has to raise the same type the HTTPS side does,
    because a status query lists the output directory over Transfer and reads
    the log over HTTPS -- a researcher whose session expired should get one
    answer, not one per surface.
    """

    def refusing_transfer_client(self, c, monkeypatch, operation: str, error):
        """Install a Transfer client that raises `error` from one operation.

        Assigned rather than patched: the real one is built on first use, so
        there is nothing on the object yet to patch, and letting it build would
        be a live call to Globus Auth.
        """

        class Refusing:
            def __getattr__(self, name):
                if name != operation:
                    raise AttributeError(name)

                def raise_it(*args, **kwargs):
                    raise error

                return raise_it

        c._tc = Refusing()

    def transfer_error(self, status: int, code: str, message: str):
        import globus_sdk
        import requests

        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({"code": code, "message": message}).encode()
        response.headers["Content-Type"] = "application/json"
        response.reason = "Unauthorized"
        response.request = requests.Request(
            "GET", "https://transfer.api.globus.org/v0.10/operation/endpoint/x/ls"
        ).prepare()
        return globus_sdk.TransferAPIError(response)

    async def test_a_lapsed_session_on_ls_raises_the_same_type(
        self, client, monkeypatch
    ):
        c, _ = client(lambda r: pytest.fail("HTTPS should not be reached"))
        error = self.transfer_error(401, "AuthenticationFailed", "Token is not active")
        self.refusing_transfer_client(c, monkeypatch, "operation_ls", error)

        with pytest.raises(GlobusSessionExpired) as refusal:
            await c.operation_ls(endpoint=COLLECTION, path="/lustre/out")
        assert "Frontier" in str(refusal.value)

    async def test_a_missing_subtree_is_still_skipped_on_a_recursive_walk(
        self, client, monkeypatch
    ):
        """The 404-swallowing that makes `recursive=True` usable has to survive
        the session check being added in front of it."""
        c, _ = client(lambda r: pytest.fail("HTTPS should not be reached"))
        error = self.transfer_error(404, "ClientError.NotFound", "no such path")
        self.refusing_transfer_client(c, monkeypatch, "operation_ls", error)

        assert (
            await c.operation_ls(
                endpoint=COLLECTION, path="/lustre/out", recursive=True
            )
            == []
        )

    async def test_an_existing_directory_is_still_a_successful_mkdir(
        self, client, monkeypatch
    ):
        """`operation_mkdir` is idempotent by swallowing Globus's "Exists".
        The session check runs first and must not claim that one."""
        c, _ = client(lambda r: pytest.fail("HTTPS should not be reached"))
        error = self.transfer_error(
            502, "ExternalError.MkdirFailed.Exists", "Path already exists"
        )
        self.refusing_transfer_client(c, monkeypatch, "operation_mkdir", error)

        await c.operation_mkdir(endpoint=COLLECTION, path="/lustre/out")
