"""Connecting Globus from the interface.

The flow itself is `scripts/get_globus_token.py`'s, so what is worth testing is
what the move to a web interface changes: a PKCE verifier that has to survive
between two requests without ever reaching the browser, one researcher's pending
flow not being reachable by another, and Globus's refusals arriving as something
a researcher can act on.

Nothing here talks to Globus. The exchange is faked, because what is being
checked is which half of the response gets stored and how failures are worded.
"""

import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from globus_sdk.exc import GlobusAPIError

from vista_backend.services import globus_auth


@pytest.fixture(autouse=True)
def no_pending_flows():
    globus_auth._pending.clear()
    yield
    globus_auth._pending.clear()


@pytest.fixture
def alice():
    return uuid.uuid4()


@pytest.fixture
def bob():
    return uuid.uuid4()


def query(url, key):
    return parse_qs(urlparse(url).query).get(key, [None])[0]


class FakeResponse:
    """What `oauth2_exchange_code_for_tokens` returns, in the shape that matters."""

    def __init__(self, data, claims=None):
        self.data = data
        self._claims = claims or {"preferred_username": "someone@ornl.gov"}

    def decode_id_token(self):
        return self._claims


def transfer_tokens(refresh="the-refresh-token", access="the-access-token"):
    """Globus puts one resource server at the top level and the rest below, and
    which one lands where is not ours to choose."""
    return {
        "resource_server": "auth.globus.org",
        "access_token": "auth-access",
        "other_tokens": [
            {
                "resource_server": "transfer.api.globus.org",
                "access_token": access,
                "refresh_token": refresh,
            }
        ],
    }


class TestTheAuthorizationAddress:
    def test_each_cluster_pins_its_own_sso_domain(self, alice):
        """Odo and Frontier authenticate against different identity providers.
        An address that pinned the wrong one would send the researcher to a login
        that cannot grant what the other cluster needs."""
        odo = globus_auth.start_login(alice, "odo")
        frontier = globus_auth.start_login(alice, "frontier")

        assert query(odo, "session_required_single_domain") == "opensso.ccs.ornl.gov"
        assert query(frontier, "session_required_single_domain") == "sso.ccs.ornl.gov"

    def test_the_verifier_never_leaves_the_server(self, alice):
        """The point of PKCE. A verifier the browser carried would make the code
        sufficient on its own."""
        url = globus_auth.start_login(alice, "odo")
        verifier = globus_auth._pending[(alice, "odo")].verifier

        assert verifier not in url
        # Its hash is what travels, and only that.
        assert query(url, "code_challenge") is not None
        assert query(url, "code_challenge_method") == "S256"

    def test_it_asks_for_no_more_than_the_script_does(self, alice):
        """1.2 showed the base transfer scope creates the collection, so the
        interface asks for exactly the consent researchers already grant."""
        scopes = query(globus_auth.start_login(alice, "odo"), "scope").split()

        assert "urn:globus:auth:scope:transfer.api.globus.org:all" in scopes
        assert not any("gcp_install" in scope for scope in scopes)

    def test_refresh_tokens_are_requested(self, alice):
        """An access token expires in an hour. Connecting once has to mean once."""
        assert query(globus_auth.start_login(alice, "odo"), "access_type") == "offline"

    def test_starting_again_replaces_the_pending_flow(self, alice):
        """Two live addresses differ only in their verifier, so a code from the
        older one fails in a way that describes nothing the researcher did."""
        globus_auth.start_login(alice, "odo")
        first = globus_auth._pending[(alice, "odo")].verifier
        globus_auth.start_login(alice, "odo")

        assert globus_auth._pending[(alice, "odo")].verifier != first
        assert len(globus_auth._pending) == 1


class TestTheExchange:
    def test_the_refresh_token_is_what_gets_stored(self, alice, monkeypatch):
        """The access token is the one that expires in an hour. Storing it would
        work for exactly one session and then fail for a reason nobody could see."""
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(transfer_tokens()),
        )

        connection = globus_auth.complete_login(alice, "odo", "a-code")

        assert connection.refresh_token == "the-refresh-token"
        assert connection.identity == "someone@ornl.gov"

    def test_the_identity_comes_from_the_signed_token(self, alice, monkeypatch):
        """Not from anything the researcher typed."""
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(
                transfer_tokens(), claims={"preferred_username": "real@ornl.gov"}
            ),
        )

        assert globus_auth.complete_login(alice, "odo", "x").identity == "real@ornl.gov"

    def test_a_response_with_no_refresh_token_is_refused(self, alice, monkeypatch):
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(transfer_tokens(refresh=None)),
        )

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "x")
        assert "lasting" in refusal.value.detail

    def test_a_response_with_no_transfer_token_is_refused(self, alice, monkeypatch):
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(
                {"resource_server": "auth.globus.org", "other_tokens": []}
            ),
        )

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "x")
        assert "Globus Transfer" in refusal.value.detail

    def test_a_spent_flow_is_not_reusable(self, alice, monkeypatch):
        """One code, one attempt. A second try on the same flow should say to
        start again rather than fail the same way twice."""
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(transfer_tokens()),
        )
        globus_auth.complete_login(alice, "odo", "a-code")

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "a-code")
        assert "expired" in refusal.value.detail


class TestWhoseFlowIsWhose:
    def test_one_researcher_cannot_complete_anothers(self, alice, bob):
        """The flows are keyed by researcher, so Bob pasting a code while Alice
        has one pending finds nothing of hers."""
        globus_auth.start_login(alice, "odo")

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(bob, "odo", "alices-code")
        assert "expired" in refusal.value.detail
        assert (alice, "odo") in globus_auth._pending

    def test_the_clusters_do_not_share_a_flow(self, alice):
        globus_auth.start_login(alice, "odo")

        with pytest.raises(HTTPException):
            globus_auth.complete_login(alice, "frontier", "a-code")


class TestExpiry:
    def test_an_old_address_is_refused_with_what_to_do(self, alice, monkeypatch):
        globus_auth.start_login(alice, "odo")
        pending = globus_auth._pending[(alice, "odo")]
        pending.started_at = time.time() - globus_auth.PENDING_TTL_SECONDS - 1

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "a-code")
        assert "Start it again" in refusal.value.detail

    def test_an_expired_flow_is_not_kept(self, alice):
        globus_auth.start_login(alice, "odo")
        globus_auth._pending[(alice, "odo")].started_at = (
            time.time() - globus_auth.PENDING_TTL_SECONDS - 1
        )

        globus_auth.start_login(alice, "frontier")

        assert (alice, "odo") not in globus_auth._pending

    def test_an_empty_code_says_so_before_asking_globus(self, alice):
        globus_auth.start_login(alice, "odo")

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "   ")
        assert "Paste the code" in refusal.value.detail
        # Not spent: the researcher still has the address open.
        assert (alice, "odo") in globus_auth._pending


class TestGlobusRefusals:
    def raise_api_error(self, status, text, code="invalid_grant"):
        """A real GlobusAPIError over a real response.

        The error derives `message`, `code` and the rest from the response as
        read-only properties, so a subclass cannot assign them, and it reads a
        `requests` response specifically. Building one is less work than keeping
        a stand-in in step with what the SDK happens to touch.
        """
        import requests

        response = requests.Response()
        response.status_code = status
        response._content = b'{"error":"%s","error_description":"%s"}' % (
            code.encode(),
            text.encode(),
        )
        response.headers["Content-Type"] = "application/json"
        response.reason = "Bad Request"
        response.request = requests.Request(
            "POST", "https://auth.globus.org/v2/oauth2/token"
        ).prepare()
        error = GlobusAPIError(response)

        def exchange(self, code):
            raise error

        return exchange

    def test_a_stale_code_names_the_old_tab(self, alice, monkeypatch):
        """By far the most common failure, and the one whose real cause the raw
        Globus message never mentions."""
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            self.raise_api_error(400, "code_verifier does not match"),
        )

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "stale")
        assert refusal.value.status_code == 400
        assert "older Globus tab" in refusal.value.detail

    def test_any_other_refusal_is_still_actionable(self, alice, monkeypatch):
        globus_auth.start_login(alice, "odo")
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            self.raise_api_error(503, "service unavailable", code="ServiceUnavailable"),
        )

        with pytest.raises(HTTPException) as refusal:
            globus_auth.complete_login(alice, "odo", "x")
        assert "Start it again" in refusal.value.detail

    def test_a_rejected_code_leaves_the_address_usable(self, alice, monkeypatch):
        """A refused code is usually a half-copied one, so the flow survives and
        the researcher can paste the whole code into the address still on
        screen. Dropping it here would make a typo cost an entire login."""
        globus_auth.start_login(alice, "odo")
        verifier = globus_auth._pending[(alice, "odo")].verifier
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            self.raise_api_error(400, "code_verifier does not match"),
        )

        with pytest.raises(HTTPException):
            globus_auth.complete_login(alice, "odo", "half-a-cod")

        assert globus_auth._pending[(alice, "odo")].verifier == verifier

        # And the retry with the whole code goes through on that same flow.
        monkeypatch.setattr(
            globus_auth.globus_sdk.NativeAppAuthClient,
            "oauth2_exchange_code_for_tokens",
            lambda self, code: FakeResponse(transfer_tokens()),
        )
        assert globus_auth.complete_login(alice, "odo", "half-a-code").refresh_token
        assert (alice, "odo") not in globus_auth._pending
