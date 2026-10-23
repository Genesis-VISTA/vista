"""
The backend's side of the collaboration server: which server is in effect, the
`/collaboration/status` check, and the HTTP client, against a real server
mounted in-process (`collab_server` in conftest) or a faked transport.
"""

import uuid

import httpx
import pytest
from pydantic import ValidationError

from harness.api import api_client
from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import collaboration, local_settings
from vista_backend.services.collaboration import (
    CollaborationError,
    CollaborationRefused,
    CollaborationUnavailable,
    VersionConflict,
)

pytestmark = pytest.mark.anyio

URL = "http://collab.test:8002"


@pytest.fixture(autouse=True)
def _no_configured_server(monkeypatch):
    """Detach from any server the host's environment or `.env` names."""
    monkeypatch.setattr(settings, "collaboration_server_url", None)


async def _set_url(session, url: str | None, display_name: str | None = None):
    update = {"collaboration_server_url": url}
    if display_name is not None:
        update["display_name"] = display_name
    await local_settings.update(session, LocalSettingsUpdate.model_validate(update))
    return await local_settings.get_public(session)


def _fake(monkeypatch, handler) -> None:
    monkeypatch.setattr(collaboration, "transport", httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Which server
# ---------------------------------------------------------------------------


async def test_no_server_means_publish_and_import_are_off(local):
    status = await collaboration.status(local)
    assert status.configured is False
    assert status.reachable is False
    assert status.reason == collaboration.NOT_CONFIGURED
    assert status.url is None and status.source is None


async def test_the_configured_server_is_the_fallback(monkeypatch, local):
    monkeypatch.setattr(settings, "collaboration_server_url", "http://dev:8002")
    assert collaboration.server_url(local) == ("http://dev:8002", "config")


async def test_the_researchers_server_wins(monkeypatch, session):
    monkeypatch.setattr(settings, "collaboration_server_url", "http://dev:8002")
    local = await _set_url(session, URL)
    assert collaboration.server_url(local) == (URL, "settings")


async def test_no_server_has_no_client(local):
    with pytest.raises(CollaborationUnavailable, match="No collaboration server"):
        collaboration.client_for(local)


@pytest.mark.parametrize(
    "given, stored",
    [
        ("http://127.0.0.1:8002", "http://127.0.0.1:8002"),
        ("  https://collab.example.org/  ", "https://collab.example.org"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_address_is_normalised(given, stored):
    update = LocalSettingsUpdate.model_validate({"collaboration_server_url": given})
    assert update.collaboration_server_url == stored


@pytest.mark.parametrize(
    "given", ["collab.example.org", "ftp://x", "http://", "http://host:abc"]
)
def test_an_address_that_is_not_http_is_refused(given):
    with pytest.raises(ValidationError, match="server's address"):
        LocalSettingsUpdate.model_validate({"collaboration_server_url": given})


async def test_the_address_is_saved_and_cleared(session):
    assert (await _set_url(session, URL)).collaboration_server_url == URL
    assert (await _set_url(session, "")).collaboration_server_url is None


# ---------------------------------------------------------------------------
# Status: never fatal, always with a reason
# ---------------------------------------------------------------------------


async def test_a_real_server_is_reachable(collab_server, session):
    local = await _set_url(session, URL)
    status = await collaboration.status(local)
    assert status.configured and status.reachable
    assert status.reason is None
    assert (status.url, status.source) == (URL, "settings")
    assert status.server_version


async def test_the_status_route(collab_server, session):
    await _set_url(session, URL)
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/status")
    assert res.status_code == 200
    assert res.json()["reachable"] is True


async def test_the_status_route_without_a_server(session):
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/status")
    assert res.status_code == 200
    assert res.json() == {
        "configured": False,
        "reachable": False,
        "reason": collaboration.NOT_CONFIGURED,
        "url": None,
        "source": None,
        "server_version": None,
    }


async def test_a_server_that_does_not_answer(monkeypatch, session):
    def refuse(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _fake(monkeypatch, refuse)
    status = await collaboration.status(await _set_url(session, URL))
    assert status.configured and not status.reachable
    assert URL in status.reason and "not answering" in status.reason
    assert "Connection refused" in status.reason


async def test_a_server_that_times_out(monkeypatch, session):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _fake(monkeypatch, slow)
    status = await collaboration.status(await _set_url(session, URL))
    assert not status.reachable
    assert "did not answer in time" in status.reason


async def test_something_that_is_not_a_collaboration_server(monkeypatch, session):
    _fake(monkeypatch, lambda request: httpx.Response(200, text="<html>hi</html>"))
    status = await collaboration.status(await _set_url(session, URL))
    assert not status.reachable
    assert "not as a VISTA collaboration server" in status.reason


async def test_a_server_without_a_version_route(monkeypatch, session):
    _fake(monkeypatch, lambda request: httpx.Response(404, json={"detail": "x"}))
    status = await collaboration.status(await _set_url(session, URL))
    assert not status.reachable
    assert "answered 404" in status.reason


async def test_a_server_speaking_another_api_is_refused(monkeypatch, session):
    _fake(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"server_version": "9.0", "api_version": 2}
        ),
    )
    status = await collaboration.status(await _set_url(session, URL))
    assert not status.reachable
    assert "API version 2" in status.reason


def test_the_backend_speaks_the_servers_api():
    from vista_collaboration_server import API_VERSION
    from vista_collaboration_server.services import identity

    assert collaboration.API_VERSION == API_VERSION
    assert collaboration.INSTALL_ID_HEADER == identity.INSTALL_ID_HEADER
    assert collaboration.DISPLAY_NAME_HEADER == identity.DISPLAY_NAME_HEADER


def test_the_servers_tables_stay_out_of_the_backends_metadata():
    """In one process, each side's `create_all` makes only its own tables."""
    from sqlmodel import SQLModel
    from vista_collaboration_server.db.schemas import CollabTable

    assert {"item", "item_version"} <= set(CollabTable.metadata.tables)
    assert not {"item", "item_version"} & set(SQLModel.metadata.tables)
    assert "local_settings" not in CollabTable.metadata.tables


@pytest.mark.parametrize("given", ["http://host:abc", "collab.example.org"])
def test_a_malformed_configured_address_stops_startup(monkeypatch, given):
    from vista_backend.config import Settings

    monkeypatch.setenv("VISTA_BACKEND_COLLABORATION_SERVER_URL", given)
    with pytest.raises(ValidationError, match="server's address"):
        Settings.model_validate({})


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_configured_address_means_none(monkeypatch, blank):
    from vista_backend.config import Settings

    monkeypatch.setenv("VISTA_BACKEND_COLLABORATION_SERVER_URL", blank)
    assert Settings.model_validate({}).collaboration_server_url is None


def test_a_configured_address_is_normalised(monkeypatch):
    from vista_backend.config import Settings

    monkeypatch.setenv("VISTA_BACKEND_COLLABORATION_SERVER_URL", " http://dev:8002/ ")
    assert Settings.model_validate({}).collaboration_server_url == "http://dev:8002"


def _redirect_to_https(request):
    if request.url.scheme == "http":
        return httpx.Response(
            301, headers={"location": str(request.url.copy_with(scheme="https"))}
        )
    return httpx.Response(200, json={"server_version": "1", "api_version": 1})


async def test_a_redirect_is_named_not_followed(monkeypatch, session):
    """A moved server is reported, with the address to use instead."""
    seen = []

    def handler(request):
        seen.append(request.url.scheme)
        return _redirect_to_https(request)

    _fake(monkeypatch, handler)
    status = await collaboration.status(await _set_url(session, URL))
    assert not status.reachable
    assert "redirects to https://collab.test:8002/version" in status.reason
    assert "use that address" in status.reason
    assert seen == ["http"]


async def test_a_publish_is_never_turned_into_a_get_by_a_redirect(
    monkeypatch, session, tmp_path
):
    """On a 301 httpx would resend a POST as a GET, without its body."""
    methods = []

    def handler(request):
        methods.append(request.method)
        return _redirect_to_https(request)

    _fake(monkeypatch, handler)
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationUnavailable, match="redirects to"):
            await client.create_item("skill", "x", manifest={})
        with pytest.raises(CollaborationUnavailable, match="redirects to"):
            await client.download_blob(
                uuid.uuid4(), 1, tmp_path / "d", blob_ref="0" * 64
            )
    assert methods == ["POST", "GET"]
    assert not (tmp_path / "d").exists()


# ---------------------------------------------------------------------------
# The client against a real server
# ---------------------------------------------------------------------------


async def test_publish_and_import_round_trip(collab_server, session, tmp_path):
    local = await _set_url(session, URL, display_name="Ångström 安")
    archive = tmp_path / "skill.tar.gz"
    archive.write_bytes(b"\x1f\x8b pretend archive")

    async with collaboration.client_for(local) as client:
        ref = await client.upload_blob(archive)
        item = await client.create_item(
            "skill", "salt-analysis", manifest={"name": "salt-analysis"}, blob_ref=ref
        )
        assert item.created_by == local.install_id
        assert item.created_by_name == "Ångström 安"
        assert item.head_version == 1
        v1 = await client.get_version(item.id, 1)
        assert (v1.parent_version, v1.blob_ref) == (None, ref)

        v2 = await client.publish_version(item.id, parent_version=1, manifest={"n": 2})
        assert v2.version == 2

        with pytest.raises(VersionConflict) as conflict:
            await client.publish_version(item.id, parent_version=1, manifest={})
        assert conflict.value.head_version == 2

        assert [i.id for i in await client.list_items("skill")] == [item.id]
        assert await client.list_items("project") == []
        assert (await client.get_item(item.id)).head_version == 2
        assert [v.version for v in await client.list_versions(item.id)] == [1, 2]
        assert (await client.get_version(item.id, 1)).manifest == {
            "name": "salt-analysis"
        }

        dest = tmp_path / "imported" / "skill.tar.gz"
        await client.download_blob(item.id, 1, dest, blob_ref=ref)
        assert dest.read_bytes() == archive.read_bytes()


async def test_a_download_must_be_the_version_published(
    collab_server, session, tmp_path
):
    """Checked as it is written: a mismatch leaves nothing behind."""
    local = await _set_url(session, URL)
    archive = tmp_path / "skill.tar.gz"
    archive.write_bytes(b"the bytes published")
    async with collaboration.client_for(local) as client:
        ref = await client.upload_blob(archive)
        item = await client.create_item("skill", "x", manifest={}, blob_ref=ref)
        dest = tmp_path / "imported" / "skill.tar.gz"
        with pytest.raises(CollaborationError, match="does not match"):
            await client.download_blob(item.id, 1, dest, blob_ref="0" * 64)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []


async def test_a_large_blob_is_streamed_up(
    collab_server, session, tmp_path, monkeypatch
):
    monkeypatch.setattr(collaboration, "UPLOAD_PART", 7)
    local = await _set_url(session, URL)
    archive = tmp_path / "kb.tar"
    archive.write_bytes(b"0123456789" * 5)
    async with collaboration.client_for(local) as client:
        ref = await client.upload_large_blob(archive)
        item = await client.create_item(
            "knowledge-base", "x", manifest={}, blob_ref=ref
        )
        dest = tmp_path / "back.tar"
        await client.download_blob(item.id, 1, dest, blob_ref=ref)
    assert dest.read_bytes() == archive.read_bytes()


async def test_client_refusals_are_named(collab_server, session, tmp_path):
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationError, match="404"):
            await client.get_item(uuid.uuid4())
        with pytest.raises(CollaborationRefused, match="422.*No blob"):
            await client.create_item("skill", "x", manifest={}, blob_ref="0" * 64)
        item = await client.create_item("skill", "x", manifest={})
        dest = tmp_path / "none"
        with pytest.raises(CollaborationError, match="no blob"):
            await client.download_blob(item.id, 1, dest, blob_ref="0" * 64)
        assert not dest.exists()
        assert list(tmp_path.glob(".none-*")) == []  # no partial download left


async def test_client_calls_to_an_unreachable_server(monkeypatch, session):
    def refuse(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _fake(monkeypatch, refuse)
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationUnavailable, match="not answering"):
            await client.list_items()


async def test_an_answer_the_client_cannot_read_is_a_collaboration_error(
    monkeypatch, session, tmp_path
):
    """A 2xx with the wrong body raises `CollaborationError`, not a parse error."""
    _fake(monkeypatch, lambda request: httpx.Response(200, json={"nope": 1}))
    local = await _set_url(session, URL)
    blob = tmp_path / "b"
    blob.write_bytes(b"x")
    async with collaboration.client_for(local) as client:
        for call in (
            client.list_items(),
            client.get_item(uuid.uuid4()),
            client.upload_blob(blob),
            client.create_item("skill", "x", manifest={}),
        ):
            with pytest.raises(CollaborationError, match="does not understand"):
                await call


async def test_a_409_without_a_head_is_a_plain_refusal(monkeypatch, session):
    _fake(monkeypatch, lambda request: httpx.Response(409, json={"detail": "busy"}))
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationRefused, match="409") as refused:
            await client.publish_version(uuid.uuid4(), parent_version=1, manifest={})
    assert not isinstance(refused.value, VersionConflict)


async def test_a_validation_error_names_what_was_wrong(collab_server, session):
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationRefused, match="422.*String should match"):
            await client.create_item("Not A Kind", "x", manifest={})


async def test_a_failed_download_by_conflict_is_a_conflict(
    monkeypatch, session, tmp_path
):
    """`download_blob` maps error statuses the way every other call does."""
    _fake(
        monkeypatch,
        lambda request: httpx.Response(409, json={"detail": "x", "head_version": 3}),
    )
    local = await _set_url(session, URL)
    async with collaboration.client_for(local) as client:
        with pytest.raises(VersionConflict):
            await client.download_blob(
                uuid.uuid4(), 1, tmp_path / "d", blob_ref="0" * 64
            )


async def test_local_file_errors_are_collaboration_errors(
    collab_server, session, tmp_path
):
    local = await _set_url(session, URL)
    blocker = tmp_path / "a-file"
    blocker.write_bytes(b"")
    async with collaboration.client_for(local) as client:
        with pytest.raises(CollaborationError, match="Could not read"):
            await client.upload_blob(tmp_path / "missing.tar.gz")
        item = await client.create_item("skill", "x", manifest={})
        with pytest.raises(CollaborationError, match="Could not write"):
            await client.download_blob(
                item.id, 1, blocker / "inside" / "d", blob_ref="0" * 64
            )
