#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cryptography>=42", "PyJWT>=2.8"]
# ///
"""
Test of scripts/dev-gateway.py: starts it in front of a fake UI and checks that
the tokens it injects pass a copy of the collaboration server's verifier
(`collaboration_server/.../services/identity.py`, `TokenVerifier.verify`), that
the sign-in works, and that a response streams through unbuffered.

    uv run --script scripts/tests/dev_gateway_test.py
"""

from __future__ import annotations

import http.client
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt

GATEWAY = Path(__file__).resolve().parent.parent / "dev-gateway.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def verify(token: str, issuer: str) -> dict:
    """A copy of `TokenVerifier.verify`'s checks, minus the HTTP errors."""
    client = jwt.PyJWKClient(f"{issuer}/protocol/openid-connect/certs", timeout=10)
    key = client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key=key,
        algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
        issuer=issuer,
        options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        leeway=30,
    )
    assert claims.get("typ") == "Bearer", claims
    assert claims.get("azp") in {"apisix"}, claims
    assert "vista" in claims.get("resource_access", {}).get("apisix", {}).get("roles", []), claims
    return claims


class FakeUI(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: one\n\n")
            self.wfile.flush()
            time.sleep(1.0)
            self.wfile.write(b"data: two\n\n")
            self.close_connection = True
            return
        token = self.headers.get("X-Access-Token", "")
        body = f"{token}\n{self.headers.get('Authorization', '')}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def request(port: int, path: str, headers: dict | None = None, method: str = "GET", body: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    return conn, conn.getresponse()


def main() -> None:
    ui_port, gw_port = free_port(), free_port()
    ui = ThreadingHTTPServer(("127.0.0.1", ui_port), FakeUI)
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    gateway = subprocess.Popen(
        [sys.executable, str(GATEWAY), "--port", str(gw_port), "--upstream", f"http://127.0.0.1:{ui_port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", gw_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        issuer = f"http://127.0.0.1:{gw_port}/realms/dev"

        # Not signed in: a browser is sent to the login page, a script gets 401.
        conn, r = request(gw_port, "/projects", {"Accept": "text/html"})
        assert r.status == 303 and r.getheader("Location").startswith("/login"), r.status
        conn.close()
        conn, r = request(gw_port, "/api/projects", {"Accept": "application/json"})
        assert r.status == 401, r.status
        conn.close()

        # Sign in as alice and bob.
        tokens = {}
        for name in ("alice", "bob"):
            conn, r = request(
                gw_port,
                "/login",
                {"Content-Type": "application/x-www-form-urlencoded"},
                "POST",
                f"name={name}&next=/projects",
            )
            assert r.status == 303 and r.getheader("Location") == "/projects", r.status
            cookie = r.getheader("Set-Cookie").split(";")[0]
            conn.close()

            # A spoofed token from the browser is replaced by the gateway's own.
            conn, r = request(gw_port, "/whoami", {"Cookie": cookie, "X-Access-Token": "forged"})
            token, auth = r.read().decode().split("\n")
            conn.close()
            assert token != "forged" and auth == "", (token, auth)
            tokens[name] = verify(token, issuer)

        assert tokens["alice"]["preferred_username"] == "alice"
        assert tokens["bob"]["preferred_username"] == "bob"
        assert tokens["alice"]["sub"] != tokens["bob"]["sub"]

        # A token signed by some other key is refused by the same checks.
        forged = jwt.encode({"iss": issuer, "sub": "x", "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256")
        try:
            verify(forged, issuer)
        except jwt.PyJWTError:
            pass
        else:
            raise AssertionError("a forged token passed")

        # A name that is not a name is refused.
        conn, r = request(
            gw_port, "/login", {"Content-Type": "application/x-www-form-urlencoded"}, "POST", "name=../etc&next=/"
        )
        assert r.status == 400, r.status
        conn.close()

        # Streaming: the first event arrives well before the upstream finishes.
        started = time.time()
        conn, r = request(gw_port, "/events", {"Cookie": "vista_dev_user=alice"})
        first = r.read1(64)
        elapsed = time.time() - started
        assert first == b"data: one\n\n" and elapsed < 0.7, (first, elapsed)
        assert r.read() == b"data: two\n\n"
        conn.close()
    finally:
        gateway.terminate()
        gateway.wait(timeout=10)
        ui.shutdown()
    print("dev gateway: ok")


if __name__ == "__main__":
    main()
