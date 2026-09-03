#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14, <3.15"
# dependencies = [
#    "globus_sdk",
#    "fastmcp>=3.1.1",
#    "pydantic-settings>=2.0",
# ]
# ///
"""Acquire the Globus-issued IRI token Vista needs for NERSC.

NERSC's IRI API is authorized by a Globus Auth token (scope
``…/ed3e577d-…/iri_api``). On NERSC a single IRI token covers both compute
(submit/status) and file ops, and it is a **per-user** credential, so this
script prints it for you to paste into the Vista UI (the Perlmutter / NERSC IRI
token field) rather than writing it to ``.env``::

    ./scripts/get_globus_token.py --cluster perlmutter

The flow uses the Globus Native App device/auth-code flow, requests refresh
tokens, and caches them under ``~/.globus/`` so re-running refreshes silently
instead of forcing another browser login.

OLCF (Odo / Frontier) no longer appears here. Those clusters used to need a
Globus Transfer refresh token because the AmSC IRI tokens carry no storage
scope, but both OLCF collections are High Assurance with a 3-day
authentication-assurance timeout that a token refresh cannot reset — unusable
for unattended operation. Their job output now comes back via an S3 push from
the compute node instead; see
``openspec/changes/replace-globus-with-s3-push/design.md``.

Ports the structure of NERSC's iri-api-get-globus-token.
See https://github.com/NERSC/iri-api-get-globus-token
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path

import globus_sdk
from globus_sdk.exc import GlobusAPIError, GlobusConnectionError

# Same Native App client ID used by NERSC's iri-api-get-globus-token;
# settings.globus_native_app_client_id resolves to the same value.
DEFAULT_CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"

AUTH_RESOURCE_SERVER = "auth.globus.org"

# NERSC IRI API: the scope is https://auth.globus.org/scopes/<RS>/iri_api, and the
# minted token's resource_server is that RS UUID. This is the token Vista passes to
# the IRI client as nersc_iri_token (compute + storage on Perlmutter).
NERSC_IRI_RESOURCE_SERVER = "ed3e577d-f7f3-4639-b96e-ff5a8445d699"
NERSC_IRI_SCOPE = f"https://auth.globus.org/scopes/{NERSC_IRI_RESOURCE_SERVER}/iri_api"

REQUIRED_AUTH_SCOPES = {
    "openid",
    "profile",
    "email",
    "urn:globus:auth:scope:auth.globus.org:view_identities",
}

REPO_ROOT = Path(__file__).resolve().parent.parent

CLUSTERS = ["perlmutter"]


def load_mcp_settings():
    """Import the vista_mcp_server settings (Native App client id)."""
    sys.path.insert(0, str(REPO_ROOT / "mcp_servers" / "vista_mcp_server" / "src"))
    from vista_mcp_server.config import settings

    return settings


def get_requested_scopes() -> list[str]:
    return sorted(REQUIRED_AUTH_SCOPES) + [NERSC_IRI_SCOPE]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Get the Globus-issued NERSC IRI access token for Vista's Perlmutter "
            "submissions. Tokens are cached to a secure local file."
        )
    )
    parser.add_argument(
        "--cluster",
        choices=CLUSTERS,
        default="perlmutter",
        help=(
            "Cluster to mint a token for. Only NERSC/Perlmutter uses a Globus-issued "
            "IRI token; OLCF clusters need no Globus credential (their job output "
            "comes back via an S3 push)."
        ),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="Path for saved token JSON (default: ~/.globus/nersc_tokens_perlmutter.json)",
    )
    parser.add_argument(
        "--client-id",
        default=DEFAULT_CLIENT_ID,
        help=f"Globus Native App client ID (default: {DEFAULT_CLIENT_ID})",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Also print the refresh token (the access token is always printed).",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Skip refresh and force interactive browser login.",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Refresh saved tokens only; do not fall back to interactive login.",
    )
    parser.add_argument(
        "--prompt-login",
        action="store_true",
        help="Add prompt=login to the authorize URL to force re-authentication.",
    )
    return parser.parse_args()


def ensure_private_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)


def load_tokens(token_file: Path) -> dict | None:
    if not token_file.exists():
        return None
    with token_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_file: Path, tokens: dict) -> None:
    ensure_private_parent_dir(token_file)
    tmp = token_file.with_suffix(".tmp")
    with os.fdopen(
        os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(tokens, f, indent=2)
    os.replace(tmp, token_file)
    os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)


def parse_scope_string(scope_string: str) -> set[str]:
    return set(scope_string.split()) if scope_string else set()


def get_token_for_resource_server(
    token_response_data: dict, resource_server: str
) -> dict | None:
    if token_response_data.get("resource_server") == resource_server:
        return token_response_data
    for token_data in token_response_data.get("other_tokens", []):
        if token_data.get("resource_server") == resource_server:
            return token_data
    return None


def require_token(token_response_data: dict, resource_server: str) -> dict:
    token = get_token_for_resource_server(token_response_data, resource_server)
    if token is None:
        raise RuntimeError(
            f"Missing token for required resource server: {resource_server}"
        )
    return token


def validate_auth_data(auth_data: dict) -> None:
    auth_token = require_token(auth_data, AUTH_RESOURCE_SERVER)
    granted = parse_scope_string(auth_token.get("scope", ""))
    missing = REQUIRED_AUTH_SCOPES - granted
    if missing:
        raise RuntimeError(f"Missing required Globus Auth scopes: {sorted(missing)}")
    require_token(auth_data, NERSC_IRI_RESOURCE_SERVER)


def interactive_login(
    client: globus_sdk.NativeAppAuthClient,
    *,
    prompt_login: bool = False,
) -> dict:
    client.oauth2_start_flow(
        requested_scopes=" ".join(get_requested_scopes()),
        refresh_tokens=True,
    )
    print("Open this URL, login, and consent:")
    prompt = "login" if prompt_login else globus_sdk.MISSING
    print(client.oauth2_get_authorize_url(prompt=prompt))
    code = input("\nEnter authorization code: ").strip()
    if not code:
        raise RuntimeError(
            "No authorization code entered. Re-run and paste the code shown "
            "by Globus after login."
        )
    try:
        token_response = client.oauth2_exchange_code_for_tokens(code)
    except GlobusAPIError as exc:
        detail = str(getattr(exc, "raw_text", "") or exc)
        if "code_verifier does not match" in detail or (
            "invalid_grant" in detail and exc.http_status == 400
        ):
            raise RuntimeError(
                "Authorization code exchange failed: PKCE code_verifier "
                "mismatch. The code you pasted came from a different "
                "authorize URL than the one this run just printed.\n\n"
                "Recover:\n"
                "  1. Close every old Globus auth tab in your browser.\n"
                "  2. Re-run this script.\n"
                "  3. Open ONLY the new URL it prints, complete login, "
                "and paste THAT code.\n"
                f"Original error: {detail}"
            ) from exc
        raise RuntimeError(
            f"Authorization code exchange failed (HTTP {exc.http_status}). "
            "Re-run and complete the Globus login flow again.\n"
            f"Detail: {detail}"
        ) from exc
    return token_response.data


def _normalize_token_expiry(token_data: dict, *, now: float | None = None) -> dict:
    """Inject absolute expires_at_seconds when only expires_in is present.

    Refresh responses return expires_in (relative). The rest of this script
    checks expires_at_seconds.
    """
    if not isinstance(token_data, dict):
        return token_data
    if "expires_at_seconds" not in token_data and token_data.get("expires_in"):
        token_data = dict(token_data)
        token_data["expires_at_seconds"] = int(
            (now if now is not None else time.time()) + int(token_data["expires_in"])
        )
    if "other_tokens" in token_data:
        token_data = dict(token_data)
        token_data["other_tokens"] = [
            _normalize_token_expiry(t, now=now)
            for t in token_data.get("other_tokens", [])
        ]
    return token_data


def refresh_token_for_resource(
    client: globus_sdk.NativeAppAuthClient,
    refresh_token: str,
) -> dict | None:
    try:
        return _normalize_token_expiry(client.oauth2_refresh_token(refresh_token).data)
    except (GlobusAPIError, GlobusConnectionError):
        return None


def set_token(stored: dict, resource_server: str, refreshed: dict) -> dict:
    """Write `refreshed` back into `stored` wherever that resource server lives.

    A token can be either the top-level token or one of `other_tokens`, depending
    on which scope Globus chose as the response's primary. Handle both so refresh
    works regardless of scope ordering.
    """
    merged = dict(stored)
    if merged.get("resource_server") == resource_server:
        others = list(merged.get("other_tokens", []))
        merged = dict(refreshed)
        merged["other_tokens"] = others
        return merged
    others = list(merged.get("other_tokens", []))
    for i, td in enumerate(others):
        if td.get("resource_server") == resource_server:
            others[i] = refreshed
            break
    else:
        others.append(refreshed)
    merged["other_tokens"] = others
    return merged


def refresh_stored_tokens(
    client: globus_sdk.NativeAppAuthClient, stored: dict
) -> dict | None:
    refreshed = dict(stored)
    any_refreshed = False

    for resource_server in (AUTH_RESOURCE_SERVER, NERSC_IRI_RESOURCE_SERVER):
        token = get_token_for_resource_server(refreshed, resource_server)
        refresh_tok = (token or {}).get("refresh_token")
        # The auth token's refresh_token can also sit at the response top level.
        if resource_server == AUTH_RESOURCE_SERVER and not refresh_tok:
            refresh_tok = stored.get("refresh_token")
        if not refresh_tok:
            continue
        new_token = refresh_token_for_resource(client, refresh_tok)
        if new_token is not None:
            refreshed = set_token(refreshed, resource_server, new_token)
            any_refreshed = True

    if not any_refreshed:
        return None
    try:
        validate_auth_data(refreshed)
    except RuntimeError:
        return None
    return refreshed


def main() -> None:
    args = parse_args()
    if args.force_login and args.refresh_only:
        raise RuntimeError("Choose only one of --force-login or --refresh-only")

    settings = load_mcp_settings()
    args.client_id = settings.globus_native_app_client_id
    if args.token_file is None:
        args.token_file = Path.home() / ".globus" / f"nersc_tokens_{args.cluster}.json"

    client = globus_sdk.NativeAppAuthClient(args.client_id)

    auth_data = None
    if not args.force_login:
        stored = load_tokens(args.token_file)
        if stored:
            auth_data = refresh_stored_tokens(client, stored)

    if auth_data is None:
        if args.refresh_only:
            raise RuntimeError(
                "Refresh-only mode failed. No usable saved refresh token was "
                "found, or refresh did not return all required tokens."
            )
        auth_data = interactive_login(
            client, prompt_login=args.prompt_login or args.force_login
        )

    validate_auth_data(auth_data)
    auth_data = _normalize_token_expiry(auth_data)
    save_tokens(args.token_file, auth_data)

    primary = require_token(auth_data, NERSC_IRI_RESOURCE_SERVER)
    expires_at = primary.get("expires_at_seconds")

    print(f"Saved token data to {args.token_file}")
    print(f"Globus client ID:   {args.client_id}")
    if expires_at:
        ttl = max(int(expires_at - time.time()), 0)
        print(f"Access token valid for ~{ttl} seconds.")
    print(f"Primary token scopes: {primary.get('scope', '')}")

    print("\n=== NERSC IRI access token ===")
    print(primary["access_token"])
    print(
        "\nPaste the token above into the Vista UI as your Perlmutter / NERSC "
        "IRI token. Re-run this script to mint a fresh one (it refreshes silently)."
    )
    if args.print_token and primary.get("refresh_token"):
        print("\nRefresh token (cached in the token file for silent refresh):")
        print(primary["refresh_token"])


if __name__ == "__main__":
    main()
