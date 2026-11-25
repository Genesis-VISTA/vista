from unittest.mock import patch

from vista_backend.agents.agents import sandbox_env
from vista_backend.services import globus_auth
from vista_backend.services.current_user import use_user


def test_sandbox_env_has_no_server_secrets():
    env = sandbox_env(
        {
            "PATH": "/bin",
            "HOME": "/h",
            "LC_ALL": "C",
            "VISTA_BACKEND_ENCRYPTION_KEY": "k",
            "VISTA_BACKEND_SLACK_BOT_TOKEN": "s",
            "VISTA_MCP_SECRET": "m",
            "OPENAI_API_KEY": "o",
        }
    )
    assert env == {"PATH": "/bin", "HOME": "/h", "LC_ALL": "C"}


def test_globus_pending_is_per_user():
    class Flow:
        def __init__(self, verifier: str) -> None:
            self.verifier = verifier

    class Client:
        def oauth2_start_flow(self, **kw):
            return Flow(f"v{len(globus_auth._pending)}")

        def oauth2_get_authorize_url(self, **kw):
            return "url"

    globus_auth._pending.clear()
    with patch.object(globus_auth, "_client", lambda: Client()):
        with use_user("alice"):
            globus_auth.start_login("odo")
        with use_user("bob"):
            globus_auth.start_login("odo")
    assert set(globus_auth._pending) == {("alice", "odo"), ("bob", "odo")}
    assert (
        globus_auth._pending[("alice", "odo")].verifier
        != globus_auth._pending[("bob", "odo")].verifier
    )
    globus_auth._pending.clear()
