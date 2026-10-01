"""
Globus client for Odo and Frontier file operations.

Vista submits jobs to OLCF through the AmSC IRI service for compute, but the
IRI service's per-user token doesn't grant storage scope; file ops therefore go
through Globus. Two Globus surfaces are used, and which does what is the whole
shape of this module:

* **Bytes** move over the **HTTPS interface** -- ordinary `GET`/`PUT` against
  the OLCF collection, authorized by the researcher's own bearer token. This
  needs no collection on Vista's side at all, which is why there is no longer a
  Globus Connect Personal endpoint anywhere in this project.
* **Metadata** -- `operation_ls`, `operation_mkdir` -- stays on the **Transfer
  API**, which has always worked and which the HTTPS interface has no answer
  for (it offers no directory listing).

Odo (open enclave) and Frontier (moderate enclave) sit behind different OLCF SSO
session domains, so there is one credential per enclave, resolved by
`UserConfig.require_globus_token(cluster)`. Each is a *pair* of refresh tokens
(`lib/types.GlobusTokens`): Globus issues one per resource server, and the
collection is its own. Per-user authorization is the S3M token project check in
`lib/olcf_token.py`.

Two things the collections' behaviour forces on callers, both established by
probing the live OLCF collections:

* A plain `GET` returns **no `Content-Length`**, and a ranged `206` reports
  `Content-Range: bytes a-b/*`. `HEAD` is the only way to learn a file's size.
* Only explicit `start-end` ranges work. Suffix ranges (`bytes=-N`) answer
  `416`, because the server streams from the filesystem without seeking to the
  end and so cannot know where the end is.

`globus_sdk` is synchronous, so Transfer calls run in worker threads; the HTTPS
side is `httpx` and is async natively. Both share the same
`RefreshTokenAuthorizer` pattern, which mints a fresh access token on first use
and renews it transparently.
"""

from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import globus_sdk
import httpx
from fastmcp.exceptions import ToolError
from globus_sdk.exc import GlobusAPIError

from ..config import settings
from .types import GlobusTokens


Cluster = Literal["odo", "frontier"]

_CHUNK_BYTES = 1024 * 1024
""" Streaming read size for `download_file` -- how much is held at once, not
what is asked for: the request is one range covering the whole file. """

_home_owners: dict[tuple[str, str], str] = {}
""" (collection id, Transfer refresh token) -> the POSIX user it maps to. """

_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
"""
Generous on read/write, short on connect. A file op crossing to OLCF can take
minutes; a collection that is not answering at all should say so quickly.
"""


class GlobusSessionExpired(ToolError):
    """The researcher's Globus session for a cluster is no longer valid.

    Its own type because the alternative -- reading a status code or matching a
    string at each call site -- is how the original bug happened: an expired
    credential reported as "no output files yet", which reads as a failed job.

    Both OLCF collections are High Assurance with a 3-day authentication
    timeout that refreshing a token does NOT reset, and Frontier queue waits
    routinely exceed that. So this is an expected outcome of a long job, not an
    exceptional one, and it must arrive saying what to do about it.
    """


class GlobusFileNotFound(ToolError):
    """The path is not there.

    Distinct from `GlobusSessionExpired` on purpose, and distinguishable at the
    wire too: a missing file answers `404 text/plain`, an unusable credential
    answers `401 application/json`. "No outputs yet" is the correct reading of
    this one and only this one.
    """


def _gare_message(body: str) -> str | None:
    """Globus's own account of why it refused, out of a GARE 401 body.

    Surfaced verbatim rather than reworded. The message names the identity
    domains the collection accepts, which is both the actual remedy and
    something VISTA would get wrong if it tried to say it itself.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    params = parsed.get("authorization_parameters")
    if isinstance(params, dict):
        message = params.get("session_message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    detail = parsed.get("detail") or parsed.get("message")
    return detail.strip() if isinstance(detail, str) and detail.strip() else None


class GlobusClient:
    """
    Async Globus client for one cluster: Transfer for metadata, HTTPS for bytes.

    Created per file operation via `create_globus_client`. Holds a
    `RefreshTokenAuthorizer` per surface, because the two are different resource
    servers; each is built on first use, mints a short-lived access token, and
    renews it when that expires.

    `cluster` is carried only so refusals can name which connection to redo --
    a researcher authorizes the two enclaves separately and is otherwise left
    guessing which one lapsed.
    """

    def __init__(
        self, *, tokens: GlobusTokens, cluster: Cluster, client_id: str | None = None
    ):
        client_id = client_id or settings.globus_native_app_client_id
        self._native_app = globus_sdk.NativeAppAuthClient(client_id)
        self.cluster = cluster
        self._tokens = tokens
        # Nothing is built here, because `RefreshTokenAuthorizer` mints an
        # access token in its constructor -- a blocking HTTP call to Globus
        # Auth. Constructing this object happens on the event loop, and a
        # status poll that touches only one of the two surfaces should not pay
        # for the other. Each is built on first use, inside the worker thread
        # that was going to make a request anyway.
        self._tc: globus_sdk.TransferClient | None = None
        self._https_authorizer: globus_sdk.RefreshTokenAuthorizer | None = None
        self._https_servers: dict[str, str] = {}

    def _authorizer(self, refresh_token: str) -> globus_sdk.RefreshTokenAuthorizer:
        """Mint an access token from a refresh token, blocking.

        A refresh token that Globus will not exchange -- revoked consent, a
        withdrawn identity, a token minted before the scope set changed -- fails
        HERE rather than on the request it was for, and as an `AuthAPIError`
        rather than as the 401 the request surfaces. Left alone it reads as
        "unable to fetch logs", which is the credential-as-data-problem this
        module exists to stop reporting.
        """
        try:
            return globus_sdk.RefreshTokenAuthorizer(
                refresh_token=refresh_token,
                auth_client=self._native_app,
            )
        except GlobusAPIError as error:
            raise self._session_expired(
                getattr(error, "message", None) or str(error)
            ) from error

    def _transfer(self) -> globus_sdk.TransferClient:
        """The Transfer client, built on first use. Call from a worker thread."""
        if self._tc is None:
            self._tc = globus_sdk.TransferClient(
                authorizer=self._authorizer(self._tokens.transfer)
            )
        return self._tc

    # --- failures ----------------------------------------------------------

    def _session_expired(self, detail: str | None) -> GlobusSessionExpired:
        remedy = (
            f"Your Globus session for {self.cluster.title()} has expired, so "
            f"VISTA cannot reach {self.cluster.title()}'s files. Reconnect "
            f"Globus for {self.cluster.title()} in the VISTA user settings. "
            "Your job and its output are untouched -- this is only the "
            "credential."
        )
        return GlobusSessionExpired(f"{remedy}\n\nGlobus said: {detail}" if detail else remedy)

    def _raise_for_transfer_error(self, error: globus_sdk.TransferAPIError) -> None:
        """Re-raise a Transfer refusal as a session expiry when that is what it is.

        Transfer reports a lapsed High Assurance session as `401
        AuthenticationFailed`, and a missing consent as `ConsentRequired`; both
        are fixed by connecting Globus again. Anything else is left alone for
        the caller to handle, which matters because `operation_mkdir` treats
        "already exists" as success.
        """
        code = error.code or ""
        if error.http_status == 401 or code in ("ConsentRequired", "AuthenticationFailed"):
            raise self._session_expired(error.message) from error

    # --- the HTTPS interface ------------------------------------------------

    async def get_https_server(self, *, collection_id: str) -> str:
        """The collection's HTTPS base URL, from Transfer, cached per client.

        Discovered rather than configured: the URL is a property of the
        collection and Globus is the only thing that knows it. Cached because
        every byte-moving call needs it and it does not change within a client's
        lifetime.
        """
        cached = self._https_servers.get(collection_id)
        if cached:
            return cached
        server = await asyncio.to_thread(self._get_https_server, collection_id)
        self._https_servers[collection_id] = server
        return server

    def _get_https_server(self, collection_id: str) -> str:
        try:
            endpoint = self._transfer().get_endpoint(collection_id)
        except globus_sdk.TransferAPIError as e:
            self._raise_for_transfer_error(e)
            raise ToolError(
                f"Globus could not describe {self.cluster.title()}'s collection "
                f"({e.message}), so VISTA does not know where to read its files."
            ) from e
        server = endpoint.get("https_server")
        if not server:
            raise ToolError(
                f"{self.cluster.title()}'s Globus collection does not advertise "
                "an HTTPS interface, so VISTA cannot read or write its files. "
                "This needs an OLCF administrator."
            )
        return str(server).rstrip("/")

    async def _url(self, collection_id: str, remote_path: str) -> str:
        base = await self.get_https_server(collection_id=collection_id)
        # `safe="/"` keeps the path separators and escapes everything else --
        # spaces in a filename would otherwise make an invalid request line.
        return f"{base}/{quote(remote_path.lstrip('/'), safe='/')}"

    def _https_auth(self) -> globus_sdk.RefreshTokenAuthorizer:
        if self._https_authorizer is None:
            self._https_authorizer = self._authorizer(self._tokens.https)
        return self._https_authorizer

    async def _auth_header(self) -> dict[str, str]:
        # Both the first build and `get_authorization_header`'s renewal on
        # expiry are blocking HTTP calls to Globus Auth.
        header = await asyncio.to_thread(
            lambda: self._https_auth().get_authorization_header()
        )
        return {"Authorization": header} if header else {}

    def _check(self, response: httpx.Response, *, remote_path: str, body: str) -> None:
        """Turn a non-2xx HTTPS answer into the right exception type.

        The two that callers branch on are separated by status code alone, so
        this never inspects a message to decide which happened.
        """
        if response.is_success:
            return
        if response.status_code == 401:
            raise self._session_expired(_gare_message(body))
        if response.status_code == 404:
            raise GlobusFileNotFound(
                f"{remote_path} is not on {self.cluster.title()}."
            )
        raise ToolError(
            f"Globus refused to serve {remote_path} on {self.cluster.title()} "
            f"(HTTP {response.status_code}): {body[:500].strip()}"
        )

    async def stat(self, *, collection_id: str, remote_path: str) -> int:
        """The file's size in bytes, via `HEAD`.

        The ONLY way to learn it. A plain `GET` on these collections returns no
        `Content-Length`, and a ranged `206` reports its total as `*`, so every
        caller that needs to know how much there is has to come through here.
        """
        url = await self._url(collection_id, remote_path)
        headers = await self._auth_header()
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = await client.head(url, headers=headers)
            body = ""
            if response.status_code == 401:
                # A HEAD has no body by definition, and the body is where the
                # GARE `session_message` lives -- the one sentence that names
                # the identity domains this collection accepts. This is the
                # first HTTPS request of every status poll, so it is where a
                # lapsed session is normally caught; losing the message here
                # would lose it almost always. One byte, on the failure path.
                body = (await client.get(url, headers={**headers, "Range": "bytes=0-0"})).text
            self._check(response, remote_path=remote_path, body=body)
        length = response.headers.get("Content-Length")
        if length is None:
            raise ToolError(
                f"Globus did not report a size for {remote_path} on "
                f"{self.cluster.title()}, so VISTA cannot tell how much of it "
                "is new."
            )
        return int(length)

    async def read_range(
        self, *, collection_id: str, remote_path: str, start: int, end: int
    ) -> bytes:
        """Bytes `start..end` inclusive, via `Range: bytes=start-end`.

        Explicit `start-end` only. A suffix range (`bytes=-N`, "the last N
        bytes") answers `416` on these collections -- not because it is
        disabled, but because the server streams from the filesystem without
        seeking to the end and so cannot answer it. Get the size from `stat`
        and compute the range.
        """
        if start > end:
            return b""
        url = await self._url(collection_id, remote_path)
        headers = {**await self._auth_header(), "Range": f"bytes={start}-{end}"}
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            self._check(response, remote_path=remote_path, body=response.text)
            if response.status_code == 206:
                return response.content
            # `200` means the range was ignored and the whole file came back --
            # a proxy in the way, most likely. Returning it whole would be a
            # silent disaster for the caller that appends at `start`: the log
            # would contain itself twice and every later poll would see a file
            # shorter than its local copy and start over. Take the slice that
            # was asked for instead.
            logging.debug(
                f"range {start}-{end} on {remote_path} answered "
                f"{response.status_code}, not 206; slicing locally"
            )
            return response.content[start : end + 1]

    async def download_file(
        self, *, collection_id: str, remote_path: str, local_path: Path
    ) -> None:
        """Stream a whole file down to `local_path`.

        Deliberately uncapped. Bulk data stays on the cluster and is processed
        by another job there; this exists for the small artifacts an agent works
        on locally, and a size limit would be a guess at which is which.

        Written to a sibling temporary file and moved into place, so an
        interrupted fetch cannot leave a half file that the caller's
        already-downloaded check would then skip forever.

        Fetched as an explicit whole-file range rather than a plain `GET`, for
        the one thing a plain `GET` cannot offer: a length to check the result
        against. With no `Content-Length` (see the module docstring) a body that
        stops early ends the stream cleanly, and the short file is moved into
        place looking complete -- which `_get_olcf_job_outputs` then never
        re-fetches. A `206` carries the length of the range it is answering, so
        a truncation is both a protocol error in `httpx` and a byte count that
        disagrees here. Length is as far as this goes: the HTTPS interface
        offers no checksum, so the `verify_checksum` the Transfer task used to
        do has no equivalent.
        """
        size = await self.stat(collection_id=collection_id, remote_path=remote_path)
        url = await self._url(collection_id, remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        partial = local_path.with_name(local_path.name + ".part")
        headers = await self._auth_header()
        if size:
            # `bytes=0--1` is not a range, and an empty file has nothing to ask
            # for; a plain GET answers it with the empty body it should.
            headers = {**headers, "Range": f"bytes=0-{size - 1}"}
        written = 0
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if not response.is_success:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._check(response, remote_path=remote_path, body=body)
                try:
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                            handle.write(chunk)
                            written += len(chunk)
                except BaseException:
                    partial.unlink(missing_ok=True)
                    raise
        if written < size:
            # Not `!=`: a `200` means the range was ignored and the whole file
            # came back (see `read_range`), and a file still being written is
            # then longer than the `HEAD` said. More than was asked for is the
            # file, less than was asked for is a fragment.
            partial.unlink(missing_ok=True)
            raise ToolError(
                f"{remote_path} on {self.cluster.title()} arrived incomplete "
                f"({written} of {size} bytes). Nothing was written to "
                f"{local_path.name}; the fetch can be retried."
            )
        partial.replace(local_path)

    async def upload_file(
        self, *, collection_id: str, local_path: Path, remote_path: str
    ) -> None:
        """`PUT` a local file to the collection.

        Does NOT create parent directories -- a `PUT` into a missing one
        answers `404` on both clusters. Call `operation_mkdir_p` first, which is
        also what keeps the directory-permissions story unchanged: directories
        still come from Transfer's `mkdir` and still inherit the mode they
        always did.

        Sent as one body rather than streamed, so the request carries a
        `Content-Length` instead of a chunked encoding the collection has never
        been asked to accept. What goes up this way is a job's source scripts;
        bulk data is produced on the cluster and stays there.

        Checked with a `HEAD` afterwards, because a `2xx` says the request
        finished, not that the whole body was stored -- and a source file that
        lands short runs on the cluster as a syntax error pointing nowhere near
        the cause. The other half of this is the caller's: a client that dies
        mid-`PUT` never sees a response at all, so it is `_sync_job_sources`
        comparing sizes on the next submission that finishes the story. Nothing
        is removed here for that reason; the wrong size is what makes the next
        attempt re-send rather than accept the name.
        """
        url = await self._url(collection_id, remote_path)
        headers = await self._auth_header()
        payload = await asyncio.to_thread(local_path.read_bytes)
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = await client.put(url, headers=headers, content=payload)
        self._check(response, remote_path=remote_path, body=response.text)
        landed = await self.stat(collection_id=collection_id, remote_path=remote_path)
        if landed != len(payload):
            raise ToolError(
                f"{local_path.name} did not land whole on {self.cluster.title()}: "
                f"{landed} of {len(payload)} bytes at {remote_path}. It will be "
                "sent again on the next submission."
            )

    # --- filesystem operations on a collection ------------------------------

    async def home_owner(self, *, collection_id: str) -> str:
        """
        The POSIX account the researcher's Globus identity is mapped to on this
        collection: the owner of their home folder, from one Transfer `stat` of
        `/~/`. It differs between enclaves (one researcher can be `jhi` on Odo
        and `hinesjr` on Frontier), so it is asked per collection, and cached per
        credential, since a mapping does not change.
        """
        key = (collection_id, self._tokens.transfer)
        if key not in _home_owners:
            entry = await asyncio.to_thread(self._operation_stat, collection_id, "/~/")
            user = entry.get("user")
            if not user:
                raise ToolError(
                    f"Globus did not say which {self.cluster.title()} account your "
                    "home folder belongs to, so VISTA cannot name your sources folder."
                )
            _home_owners[key] = user
        return _home_owners[key]

    def _operation_stat(self, endpoint: str, path: str) -> dict[str, Any]:
        try:
            return self._transfer().operation_stat(endpoint, path=path).data
        except globus_sdk.TransferAPIError as e:
            self._raise_for_transfer_error(e)
            raise

    async def operation_ls(
        self, *, endpoint: str, path: str, recursive: bool = False,
        exclude_segments: tuple[str, ...] = (),
        max_dirs: int = 200,
    ) -> list[dict[str, Any]]:
        """
        List entries under `path` on the given collection. Returns a list of dicts
        each with at least `name`, `type` ("file"/"dir"), `size`, plus a `path`
        key injected by this wrapper holding the absolute path.

        On the Transfer API, because the HTTPS interface has no directory
        listing at all -- that division is why the Transfer half of this module
        survives.

        When `recursive=True`, BFS-walks the tree (Globus has no native recursive
        ls). Subtree errors during the walk are logged and skipped.

        The walk costs ONE API round-trip per directory, sequentially, so an
        unbounded tree (a build dir, a venv, a .git) turns a status check into
        minutes of silent waiting. Two guards:

          - `exclude_segments`: directory names never descended into. Filtering
            these out of the *results* afterwards does not help — the cost is the
            traversal, so they must be pruned from the queue.
          - `max_dirs`: hard ceiling on directories visited. On hitting it the walk
            stops and logs; callers get a partial listing rather than a hang.
        """
        return await asyncio.to_thread(
            self._operation_ls, endpoint, path, recursive, exclude_segments, max_dirs,
        )

    def _operation_ls(
        self, endpoint: str, path: str, recursive: bool,
        exclude_segments: tuple[str, ...] = (), max_dirs: int = 200,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        queue = [path]
        visited = 0
        while queue:
            cur = queue.pop(0)
            if recursive and visited >= max_dirs:
                logging.warning(
                    f"globus ls stopped at {max_dirs} directories under {path}; "
                    f"{len(queue)} subtree(s) not walked. Listing is partial."
                )
                break
            visited += 1
            try:
                resp = self._transfer().operation_ls(endpoint, path=cur)
            except globus_sdk.TransferAPIError as e:
                # Before the recursive/not split below: an expired session is
                # not a missing subtree, and swallowing it here is exactly how
                # a lapsed credential came to read as an empty output dir.
                self._raise_for_transfer_error(e)
                if recursive and e.http_status == 404:
                    continue
                if not recursive:
                    if e.http_status == 404:
                        # The same type the HTTPS side raises, so a caller can
                        # tell "not there" from "could not look".
                        raise GlobusFileNotFound(
                            f"{cur} is not on {self.cluster.title()}."
                        ) from e
                    raise
                logging.debug(f"globus ls subtree skipped ({cur}): {e.message}")
                continue
            cur_norm = cur.rstrip("/")
            for entry in resp.get("DATA", []):
                name = entry.get("name", "")
                if not name:
                    continue
                full = f"{cur_norm}/{name}" if cur_norm else name
                entry["path"] = full
                results.append(entry)
                if recursive and entry.get("type") == "dir":
                    if name in exclude_segments:
                        continue  # prune: never pay for a venv / .git / __pycache__
                    queue.append(full)
            if not recursive:
                break
        return results

    async def operation_mkdir(self, *, endpoint: str, path: str) -> None:
        """
        Create a single directory on the collection. Idempotent: succeeds if the
        dir already exists (Globus returns ExternalError.MkdirFailed.Exists, which
        we swallow). Fails if the parent doesn't exist — use `operation_mkdir_p`
        for `mkdir -p` semantics.
        """
        await asyncio.to_thread(self._operation_mkdir, endpoint, path)

    def _operation_mkdir(self, endpoint: str, path: str) -> None:
        try:
            self._transfer().operation_mkdir(endpoint, path=path)
        except globus_sdk.TransferAPIError as e:
            self._raise_for_transfer_error(e)
            code = (e.code or "") + " " + (e.message or "")
            if "Exists" in code or "exists" in code:
                return
            raise

    async def operation_mkdir_p(
        self, *, endpoint: str, path: str, parents_below: str,
    ) -> None:
        """
        `mkdir -p` equivalent for a Globus collection: idempotently create
        `path` plus any missing intermediate dirs BELOW `parents_below`.

        `parents_below` is the deepest ancestor we assume already exists (and
        do NOT try to create). For Vista's Frontier path this is the user's
        `frontier_remote_dir` — Globus would reject mkdir of /lustre, /lustre/orion,
        etc. anyway, and walking that high is wasteful.

        Globus's MKD is one-level-only, so this helper walks the suffix between
        `parents_below` and `path` and calls `operation_mkdir` for each segment.
        Already-existing segments are no-ops thanks to operation_mkdir's
        Exists-swallowing behavior.

        Still Transfer's job, and still the only way directories get made: an
        HTTPS `PUT` does not create its parents, so every upload depends on this
        having run first.
        """
        path = path.rstrip("/")
        parents_below = parents_below.rstrip("/")
        if not (path == parents_below or path.startswith(parents_below + "/")):
            raise ValueError(
                f"path {path!r} is not under parents_below {parents_below!r}"
            )
        suffix = path[len(parents_below):].lstrip("/")
        if not suffix:
            return
        current = parents_below
        for part in suffix.split("/"):
            current = f"{current}/{part}"
            await self.operation_mkdir(endpoint=endpoint, path=current)


def create_globus_client(*, tokens: GlobusTokens, cluster: Cluster) -> GlobusClient:
    """ Construct a GlobusClient. The authorizers auto-refresh their access tokens. """
    return GlobusClient(tokens=tokens, cluster=cluster)
