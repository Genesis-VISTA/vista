#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cryptography>=42", "PyJWT>=2.8"]
# ///
"""
A development stand-in for Obsidian's gateway, for `./launch.sh --hosted`.
Never packaged, never deployed: it signs in anyone who types a name.

It does what the gateway and its Keycloak realm do for VISTA:

  * serves its signing key as a JWKS at
    `{issuer}/protocol/openid-connect/certs` (issuer: `http://127.0.0.1:3100/realms/dev`),
    which is where a hosted backend looks for it;
  * shows a login page where the developer types a name, and remembers it in a
    cookie (`/login`, `/logout`);
  * reverse-proxies everything else to the UI, adding `X-Access-Token`: a fresh
    access token for that name, in the shape the gateway produces (RS256, `typ`
    Bearer, `azp` apisix, role `apisix/vista`). Responses stream through
    unbuffered, so server-sent events work.

Two browsers (or one and a private window) signed in as `alice` and `bob` are
two researchers. Listens on loopback only.

Not supported: WebSocket upgrades. `next dev` (Next 16) does not start a page
without its hot-reload socket, so behind this gateway the page renders and then
does nothing: `launch.sh --hosted` always runs the built UI (`next start`).
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import sys
import time
import uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

COOKIE = "vista_dev_user"
REALM_PATH = "/realms/dev"
JWKS_PATH = f"{REALM_PATH}/protocol/openid-connect/certs"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
TOKEN_LIFETIME = 300
# Stable across restarts, so a name is the same researcher every run.
NAMESPACE = uuid.UUID("5f0d6b1e-3c2a-4a55-9d0e-76a1d7d0c0de")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}  # fmt: skip


class Issuer:
    """The development token issuer: one RSA key, tokens minted for a name."""

    def __init__(self, issuer: str):
        self.issuer = issuer
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        self.kid = uuid.uuid4().hex
        public.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [public]}
        self._pem = self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def mint(self, name: str, *, lifetime: int = TOKEN_LIFETIME) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": str(uuid.uuid5(NAMESPACE, name)),
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid.uuid4().hex,
            "typ": "Bearer",
            "azp": "apisix",
            "preferred_username": name,
            "name": name.replace(".", " ").replace("_", " ").replace("-", " ").title(),
            "email": f"{name}@dev.invalid",
            "resource_access": {"apisix": {"roles": ["vista"]}},
        }
        return jwt.encode(claims, self._pem, algorithm="RS256", headers={"kid": self.kid})


def login_page(next_path: str, error: str = "") -> bytes:
    note = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VISTA dev sign-in</title>
<style>
  body {{ font: 16px system-ui, sans-serif; max-width: 26rem; margin: 18vh auto; padding: 0 1rem; }}
  input, button {{ font: inherit; padding: .5rem .7rem; }}
  .err {{ color: #b00020; }} small {{ color: #666; }}
</style>
<h1>VISTA, development gateway</h1>
<p>Pick a name. Each name is a different researcher; use another browser, or a private window, for a second one.</p>
{note}
<form method="post" action="/login">
  <input type="hidden" name="next" value="{html.escape(next_path, quote=True)}">
  <input name="name" placeholder="alice" autofocus required pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{{0,39}}">
  <button>Sign in</button>
</form>
<p><small>Development only: this signs in anyone who types a name.</small></p>
""".encode()


def make_handler(issuer: Issuer, upstream: tuple[str, int]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "vista-dev-gateway"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Request lines only: never a header, so never a token.
            sys.stderr.write(f"{self.address_string()} {format % args}\n")

        # -- helpers -------------------------------------------------------

        def _user(self) -> str | None:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = jar.get(COOKIE)
            return morsel.value if morsel and NAME.match(morsel.value) else None

        def _send(self, status: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8", **headers: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _redirect(self, location: str, **headers: str):
            self._send(HTTPStatus.SEE_OTHER, b"", Location=location, **headers)

        # -- the gateway's own pages ----------------------------------------

        def _own(self, path: str) -> bool:
            if path == JWKS_PATH:
                self._send(200, json.dumps(issuer.jwks).encode(), "application/json")
                return True
            if path == "/login":
                if self.command == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    form = parse_qs(self.rfile.read(min(length, 4096)).decode("utf-8", "replace"))
                    name = (form.get("name", [""])[0]).strip()
                    nxt = form.get("next", ["/"])[0]
                    if not nxt.startswith("/") or nxt.startswith("//"):
                        nxt = "/"
                    if not NAME.match(name):
                        self._send(400, login_page(nxt, "Use letters, digits, dot, dash or underscore."))
                    else:
                        self._redirect(nxt, Set_Cookie=f"{COOKIE}={name}; Path=/; HttpOnly; SameSite=Lax")
                    return True
                query = parse_qs(urlsplit(self.path).query)
                self._send(200, login_page(query.get("next", ["/"])[0]))
                return True
            if path == "/logout":
                self._redirect("/login", Set_Cookie=f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
                return True
            return False

        # -- the proxy ------------------------------------------------------

        def _proxy(self, name: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers: dict[str, str] = {}
            for key, value in self.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low in ("x-access-token", "authorization", "content-length"):
                    continue
                if low == "cookie":
                    # Our own cookie stays here; everything else is the UI's.
                    kept = "; ".join(
                        part for part in value.split("; ") if not part.startswith(f"{COOKIE}=")
                    )
                    if not kept:
                        continue
                    value = kept
                headers[key] = value
            headers["X-Access-Token"] = issuer.mint(name)
            headers["X-Forwarded-For"] = self.client_address[0]
            if body is not None:
                headers["Content-Length"] = str(len(body))
            conn = http.client.HTTPConnection(*upstream, timeout=3600)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
            except OSError as error:
                self._send(502, f"The UI at {upstream[0]}:{upstream[1]} is not answering: {error}".encode(), "text/plain")
                conn.close()
                return
            try:
                self.send_response(resp.status)
                for key, value in resp.getheaders():
                    if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                        self.send_header(key, value)
                # Close-delimited: no length to know in advance, so events can flow.
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if self.command == "HEAD" or resp.status in (204, 304):
                    return
                while chunk := resp.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # The browser went away; the stream ends with it.
            finally:
                conn.close()

        def _handle(self) -> None:
            path = urlsplit(self.path).path
            if self._own(path):
                return
            name = self._user()
            if name is None:
                if "text/html" in self.headers.get("Accept", ""):
                    self._redirect(f"/login?next={quote(self.path, safe='/')}")
                else:
                    self._send(401, b'{"detail":"Not signed in."}', "application/json")
                return
            self._proxy(name)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=3100)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--upstream", default="http://localhost:3000", help="the UI to put the sign-in in front of")
    args = parser.parse_args(argv)

    target = urlsplit(args.upstream)
    issuer = Issuer(f"http://127.0.0.1:{args.port}{REALM_PATH}")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(issuer, (target.hostname or "localhost", target.port or 80)))
    server.daemon_threads = True
    print(f"dev gateway: http://127.0.0.1:{args.port}  ->  {args.upstream}", flush=True)
    print(f"issuer:      {issuer.issuer}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
