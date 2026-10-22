"""
The collaboration server, from this backend's side: which one, whether it
answers, and an HTTP client for its item/version API.

Collaboration is explicit publish and import, so nothing here runs on its own:
without a server configured, or with one that does not answer, everything else
in VISTA works as before and only publishing and importing are off -- with the
reason, from `status`, for the interface to show. Nothing in this module raises
out of `status`; the client's methods raise `CollaborationError` and nothing
else, for the code that publishes or imports to report.

The server trusts the identity headers sent here (`install_id` and the display
name from the settings row). That is a stub, like the server's side of it.
"""

import functools
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from ..config import settings
from ..db.schemas import LocalSettingsPublic

API_VERSION = 1
"""
The collaboration server API this backend speaks. A server reporting another is
refused by name, rather than half-understood.
"""

INSTALL_ID_HEADER = "X-Vista-Install-Id"
DISPLAY_NAME_HEADER = "X-Vista-Display-Name"

STATUS_TIMEOUT = httpx.Timeout(5.0)
""" How long the status check waits: it is shown in Settings, so it must be quick. """
TRANSFER_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

transport: httpx.AsyncBaseTransport | None = None
"""
The transport every client uses; `None` is the network. Tests set it to an
`httpx.ASGITransport` over the collaboration server's app, in-process.
"""

NOT_CONFIGURED = (
    "No collaboration server is set. Add its address under Settings → "
    "Collaboration to publish and import."
)


class CollaborationError(Exception):
    """Something the researcher should be told; the message says what."""


class CollaborationUnavailable(CollaborationError):
    """No server is configured, it does not answer, or it is not one we speak to."""


class CollaborationRefused(CollaborationError):
    """The server answered with an error status."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class VersionConflict(CollaborationRefused):
    """A publish named a parent that is no longer the item's head."""

    def __init__(self, message: str, head_version: int):
        super().__init__(message, 409)
        self.head_version = head_version


class CollaborationStatus(BaseModel):
    configured: bool
    """ A server address is set, in Settings or in this installation's configuration. """
    reachable: bool
    """ It answered `/version` with the API this backend speaks. """
    reason: str | None
    """ Why publishing and importing are off; `None` when they are on. """
    url: str | None
    """ The address in effect. """
    source: Literal["settings", "config"] | None
    """ Whether the address is the researcher's, from Settings, or the installation's. """
    server_version: str | None = None


class ServerInfo(BaseModel):
    server_version: str
    api_version: int


class RemoteItem(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    created_by: uuid.UUID
    created_by_name: str | None
    head_version: int
    created_at: str


class RemoteVersion(BaseModel):
    item_id: uuid.UUID
    version: int
    parent_version: int | None
    manifest: dict[str, Any]
    blob_ref: str | None
    created_by: uuid.UUID
    created_by_name: str | None
    created_at: str


class _Blob(BaseModel):
    blob_ref: str


class _Conflict(BaseModel):
    detail: str
    head_version: int


@functools.cache
def _adapter(shape: Any) -> TypeAdapter:
    """One `TypeAdapter` per answer shape, built on first use."""
    return TypeAdapter(shape)


def server_url(
    local: LocalSettingsPublic,
) -> tuple[str | None, Literal["settings", "config"] | None]:
    """
    The server address in effect, and where it came from. Both sources are
    validated and normalised the same way (`utils.types.HttpUrl`).
    """
    if local.collaboration_server_url:
        return local.collaboration_server_url, "settings"
    if settings.collaboration_server_url:
        return settings.collaboration_server_url, "config"
    return None, None


def _detail(response: httpx.Response) -> str:
    """The server's `detail`: a string, or FastAPI's list of validation errors."""
    try:
        detail = response.json().get("detail")
    except ValueError, AttributeError:
        detail = None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [
            str(d.get("msg")) if isinstance(d, dict) and "msg" in d else str(d)
            for d in detail
        ]
        if messages:
            return "; ".join(messages)
    return response.reason_phrase


class CollaborationClient:
    """
    The collaboration server's item/version API. Use as `async with`.

    Every method raises `CollaborationError` and nothing else:
    `CollaborationUnavailable` when the server cannot be reached or redirects
    elsewhere, `VersionConflict` for a publish on a stale head,
    `CollaborationRefused` for any other error status, and the base class for
    an answer it cannot read or a local file it cannot read or write.
    """

    def __init__(
        self,
        base_url: str,
        *,
        install_id: uuid.UUID,
        display_name: str | None,
        timeout: httpx.Timeout = TRANSFER_TIMEOUT,
    ):
        self.base_url = base_url
        headers = {INSTALL_ID_HEADER: str(install_id)}
        if display_name:
            # A header value is Latin-1 at best; the server percent-decodes it.
            headers[DISPLAY_NAME_HEADER] = quote(display_name, safe="")
        # Redirects are not followed: on a 301 or 302 httpx would turn a publish
        # into a GET and drop its body, and would send the identity headers to
        # wherever the redirect points. `_check` names the new address instead.
        self._http = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )

    async def __aenter__(self) -> "CollaborationClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    def _unreachable(self, error: httpx.HTTPError) -> CollaborationUnavailable:
        if isinstance(error, httpx.TimeoutException):
            why = "it did not answer in time"
        else:
            why = str(error) or type(error).__name__
        return CollaborationUnavailable(
            f"The collaboration server at {self.base_url} is not answering ({why})."
        )

    def _check(self, response: httpx.Response, method: str, path: str) -> None:
        """Raise for a redirect or an error status. The body must have been read."""
        if response.is_redirect:
            location = response.headers.get("location", "another address")
            raise CollaborationUnavailable(
                f"The collaboration server at {self.base_url} redirects to "
                f"{location}; use that address in Settings → Collaboration."
            )
        if response.status_code == 409:
            try:
                conflict = _Conflict.model_validate(response.json())
            except ValueError:
                pass
            else:
                raise VersionConflict(conflict.detail, conflict.head_version)
        if response.is_error:
            raise CollaborationRefused(
                f"The collaboration server refused {method} {path} "
                f"({response.status_code}): {_detail(response)}",
                response.status_code,
            )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise self._unreachable(error) from error
        except (OSError, httpx.StreamError) as error:
            # Reading an uploaded file while httpx streams it.
            raise CollaborationError(
                f"Could not send {method} {path}: {error}"
            ) from error
        self._check(response, method, path)
        return response

    async def _call[T](self, shape: type[T], method: str, path: str, **kwargs) -> T:
        """Send a request and read its answer as `shape`, a model or `list[model]`."""
        response = await self._request(method, path, **kwargs)
        try:
            return _adapter(shape).validate_python(response.json())
        except (ValueError, ValidationError) as error:
            raise CollaborationError(
                f"The collaboration server at {self.base_url} answered {method} "
                f"{path} with something VISTA does not understand."
            ) from error

    async def version(self) -> ServerInfo:
        """What the server is; refuses one that is not a server this backend speaks to."""
        try:
            info = await self._call(ServerInfo, "GET", "/version")
        except CollaborationUnavailable:
            raise
        except CollaborationRefused as error:
            raise CollaborationUnavailable(
                f"The collaboration server at {self.base_url} answered "
                f"{error.status_code} when asked its version."
            ) from error
        except CollaborationError as error:
            raise CollaborationUnavailable(
                f"{self.base_url} answered, but not as a VISTA collaboration server."
            ) from error
        if info.api_version != API_VERSION:
            raise CollaborationUnavailable(
                f"The collaboration server at {self.base_url} speaks API version "
                f"{info.api_version}; this VISTA speaks version {API_VERSION}."
            )
        return info

    async def list_items(self, kind: str | None = None) -> list[RemoteItem]:
        params = {"kind": kind} if kind else None
        return await self._call(list[RemoteItem], "GET", "/items", params=params)

    async def create_item(
        self,
        kind: str,
        name: str,
        *,
        manifest: dict[str, Any],
        blob_ref: str | None = None,
    ) -> RemoteItem:
        """A new item, published with its version 1."""
        body = {"kind": kind, "name": name, "manifest": manifest, "blob_ref": blob_ref}
        return await self._call(RemoteItem, "POST", "/items", json=body)

    async def get_item(self, item_id: uuid.UUID) -> RemoteItem:
        return await self._call(RemoteItem, "GET", f"/items/{item_id}")

    async def list_versions(self, item_id: uuid.UUID) -> list[RemoteVersion]:
        return await self._call(
            list[RemoteVersion], "GET", f"/items/{item_id}/versions"
        )

    async def get_version(self, item_id: uuid.UUID, version: int) -> RemoteVersion:
        return await self._call(
            RemoteVersion, "GET", f"/items/{item_id}/versions/{version}"
        )

    async def upload_blob(self, path: Path) -> str:
        """Upload a file and return its blob reference, for a version to name."""
        try:
            file = path.open("rb")
        except OSError as error:
            raise CollaborationError(f"Could not read {path}: {error}") from error
        with file:
            blob = await self._call(
                _Blob,
                "POST",
                "/blobs",
                files={"file": (path.name, file, "application/octet-stream")},
            )
        return blob.blob_ref

    async def publish_version(
        self,
        item_id: uuid.UUID,
        *,
        parent_version: int,
        manifest: dict[str, Any],
        blob_ref: str | None = None,
    ) -> RemoteVersion:
        """
        Publish on top of `parent_version`. Raises `VersionConflict`, carrying
        the head, when that is no longer it.
        """
        body = {
            "parent_version": parent_version,
            "manifest": manifest,
            "blob_ref": blob_ref,
        }
        return await self._call(
            RemoteVersion, "POST", f"/items/{item_id}/versions", json=body
        )

    async def download_blob(
        self,
        item_id: uuid.UUID,
        version: int,
        dest: Path,
        *,
        max_bytes: int | None = None,
    ) -> None:
        """
        Stream a version's blob to `dest`, which appears only once complete.
        With `max_bytes`, a larger blob is refused before more of it is written.
        """
        path = f"/items/{item_id}/versions/{version}/blob"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}-")
        except OSError as error:
            raise CollaborationError(f"Could not write {dest}: {error}") from error
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                try:
                    async with self._http.stream("GET", path) as response:
                        if response.is_error or response.is_redirect:
                            await response.aread()
                            self._check(response, "GET", path)
                        received = 0
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if max_bytes is not None and received > max_bytes:
                                raise CollaborationError(
                                    f"The collaboration server sent more than "
                                    f"{max_bytes // (1024 * 1024)} MB for {path}; "
                                    "refused."
                                )
                            out.write(chunk)
                except httpx.HTTPError as error:
                    raise self._unreachable(error) from error
            os.replace(tmp, dest)
        except OSError as error:
            tmp.unlink(missing_ok=True)
            raise CollaborationError(f"Could not write {dest}: {error}") from error
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def client_for(
    local: LocalSettingsPublic, *, timeout: httpx.Timeout = TRANSFER_TIMEOUT
) -> CollaborationClient:
    """A client on the server in effect, as this installation. Raises when none is set."""
    url, _ = server_url(local)
    if url is None:
        raise CollaborationUnavailable(NOT_CONFIGURED)
    return CollaborationClient(
        url,
        install_id=local.install_id,
        display_name=local.display_name,
        timeout=timeout,
    )


async def status(local: LocalSettingsPublic) -> CollaborationStatus:
    """
    Whether publishing and importing can work right now, and if not, why.
    Never raises: an unreachable server is a state to show, not a failure.
    """
    url, source = server_url(local)
    if url is None:
        return CollaborationStatus(
            configured=False,
            reachable=False,
            reason=NOT_CONFIGURED,
            url=None,
            source=None,
        )
    try:
        async with client_for(local, timeout=STATUS_TIMEOUT) as client:
            info = await client.version()
    except CollaborationError as error:
        return CollaborationStatus(
            configured=True, reachable=False, reason=str(error), url=url, source=source
        )
    return CollaborationStatus(
        configured=True,
        reachable=True,
        reason=None,
        url=url,
        source=source,
        server_version=info.server_version,
    )
