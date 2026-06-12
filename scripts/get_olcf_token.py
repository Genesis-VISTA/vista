#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14, <3.15"
# dependencies = [
#    "globus_sdk",
#    "fastmcp>=3.1.1",
#    "pydantic-settings>=2.0",
# ]
# ///
"""Acquire Globus Transfer tokens for OLCF -> local transfers.

Ports the structure of get_globus_token.py to OLCF.

The usual invocation is per-cluster: --cluster odo|frontier pulls the
collection ID, SSO session domain, and Native App client ID from the
vista_mcp_server config, and --save-env writes the resulting refresh token
into .env at the repo root:

    ./scripts/get_olcf_token.py --cluster odo --save-env
    ./scripts/get_olcf_token.py --cluster frontier --save-env

By default it requests only the Globus Transfer API scope
(transfer.api.globus.org:all), which is correct for OLCF's Globus 4 DTN
endpoint (UUID ef1a9560-7ca1-11e5-992c-22000b96db58) -- that endpoint is
activated with endpoint_autoactivate() rather than a data_access scope.

If you are transferring from a newer OLCF Globus Connect Server 5 mapped
collection, pass --data-access to also request that collection's
data_access dependent scope in the same consent.

Tokens are cached and refreshed automatically. The Transfer access token
saved here is what olcf_transfer.py consumes to submit a transfer task.

See https://github.com/jqyin/OLCF-Globus-Transfer/blob/main/get_olcf_token.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import globus_sdk
from globus_sdk.exc import GlobusAPIError, GlobusConnectionError

# Same Native App client ID used by get_globus_token.py.
DEFAULT_CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"

# OLCF DTN (NCCS Open DTN) Globus collection UUID. Override with
# --olcf-collection-id if you are transferring from a different OLCF
# collection (e.g. an HPSS or project-specific GCS5 collection).
DEFAULT_OLCF_COLLECTION_ID = "ef1a9560-7ca1-11e5-992c-22000b96db58"

TRANSFER_RESOURCE_SERVER = "transfer.api.globus.org"
AUTH_RESOURCE_SERVER = "auth.globus.org"

REQUIRED_AUTH_SCOPES = {
    "openid",
    "profile",
    "email",
    "urn:globus:auth:scope:auth.globus.org:view_identities",
}
TRANSFER_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two OLCF enclaves authenticate against different SSO domains.
CLUSTER_SESSION_DOMAINS = {
    "odo": "opensso.ccs.ornl.gov",
    "frontier": "sso.ccs.ornl.gov",
}


def load_mcp_settings():
    """Import the vista_mcp_server settings (collection IDs, client ID)."""
    sys.path.insert(0, str(REPO_ROOT / "mcp_servers" / "vista_mcp_server" / "src"))
    from vista_mcp_server.config import settings

    return settings


def build_transfer_scope(collection_id: str | None) -> str:
    """Return the Transfer scope, optionally with a data_access dependency.

    Globus Connect Server 5 mapped collections require a per-collection
    data_access scope on top of the Transfer scope. Pass collection_id to
    request both in one consent. Globus 4 endpoints (like the legacy OLCF
    DTN) do not have a data_access scope -- pass None to request only the
    base Transfer scope.
    """
    if not collection_id:
        return TRANSFER_SCOPE
    data_access = f"https://auth.globus.org/scopes/{collection_id}/data_access"
    return f"{TRANSFER_SCOPE}[{data_access}]"


def get_requested_scopes(data_access_collection_id: str | None) -> list[str]:
    return sorted(REQUIRED_AUTH_SCOPES) + [
        build_transfer_scope(data_access_collection_id)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Get Globus Auth + Transfer tokens for OLCF transfers. "
            "Tokens are saved to a secure local file by default."
        )
    )
    parser.add_argument(
        "--cluster",
        choices=sorted(CLUSTER_SESSION_DOMAINS),
        default=None,
        help=(
            "Pull the OLCF collection ID, session domain, and client ID for "
            "this cluster from the vista_mcp_server config (overrides "
            "--olcf-collection-id, --session-domain, and --client-id)."
        ),
    )
    parser.add_argument(
        "--save-env",
        action="store_true",
        help=(
            "Write the refresh token to .env at the repo root as "
            "VISTA_MCP_<CLUSTER>_GLOBUS_REFRESH_TOKEN (requires --cluster). "
        ),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help=(
            "Path for saved token JSON (default: ~/.globus/olcf_tokens.json, "
            "or ~/.globus/olcf_tokens_<cluster>.json with --cluster)"
        ),
    )
    parser.add_argument(
        "--olcf-collection-id",
        default=DEFAULT_OLCF_COLLECTION_ID,
        help=(
            "UUID of the OLCF Globus collection you will transfer from "
            f"(default: {DEFAULT_OLCF_COLLECTION_ID}). Only used when "
            "--data-access is also set."
        ),
    )
    parser.add_argument(
        "--data-access",
        action="store_true",
        help=(
            "Also request the data_access dependent scope for "
            "--olcf-collection-id. Required for Globus Connect Server 5 "
            "mapped collections; must NOT be set for Globus 4 endpoints "
            "like the legacy OLCF DTN (which would return UNKNOWN_SCOPE_ERROR)."
        ),
    )
    parser.add_argument(
        "--session-domain",
        default=None,
        help=(
            "Force the Globus authorize URL to require an authenticated "
            "identity from this domain (session_required_single_domain). "
            "Use 'sso.ccs.ornl.gov' for OLCF -- the OLCF GCS5 collection "
            "rejects sessions that lack an OLCF SSO identity."
        ),
    )
    parser.add_argument(
        "--client-id",
        default=DEFAULT_CLIENT_ID,
        help=f"Globus Native App client ID (default: {DEFAULT_CLIENT_ID})",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Print the Transfer access token to stdout (off by default).",
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


def update_env_file(env_file: Path, key: str, value: str) -> None:
    """Set key=value in env_file, replacing an existing (possibly commented-out) line."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}=")
    match_idx = next((i for i in range(len(lines) - 1, -1, -1) if pattern.match(lines[i])), None)
    if match_idx is not None:
        lines[match_idx] = f"{key}={value}"
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_file, stat.S_IRUSR | stat.S_IWUSR)


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
    require_token(auth_data, TRANSFER_RESOURCE_SERVER)


def interactive_login(
    client: globus_sdk.NativeAppAuthClient,
    data_access_collection_id: str | None,
    *,
    prompt_login: bool = False,
    session_domain: str | None = None,
) -> dict:
    client.oauth2_start_flow(
        requested_scopes=" ".join(get_requested_scopes(data_access_collection_id)),
        refresh_tokens=True,
    )
    print("Open this URL, login, and consent:")
    prompt = "login" if prompt_login else globus_sdk.MISSING
    url_kwargs: dict = {"prompt": prompt}
    if session_domain:
        url_kwargs["session_required_single_domain"] = session_domain
    print(client.oauth2_get_authorize_url(**url_kwargs))
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
    and the downstream transfer/list scripts check expires_at_seconds.
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


def merge_top_level_auth(stored: dict, refreshed_auth: dict) -> dict:
    merged = dict(refreshed_auth)
    merged["other_tokens"] = list(stored.get("other_tokens", []))
    return merged


def replace_other_token(stored: dict, resource_server: str, refreshed: dict) -> dict:
    merged = dict(stored)
    other = list(merged.get("other_tokens", []))
    for i, td in enumerate(other):
        if td.get("resource_server") == resource_server:
            other[i] = refreshed
            break
    else:
        other.append(refreshed)
    merged["other_tokens"] = other
    return merged


def refresh_stored_tokens(
    client: globus_sdk.NativeAppAuthClient, stored: dict
) -> dict | None:
    refreshed = dict(stored)
    any_refreshed = False

    auth_rt = stored.get("refresh_token") or (
        get_token_for_resource_server(stored, AUTH_RESOURCE_SERVER) or {}
    ).get("refresh_token")
    if auth_rt:
        new_auth = refresh_token_for_resource(client, auth_rt)
        if new_auth is not None:
            refreshed = merge_top_level_auth(refreshed, new_auth)
            any_refreshed = True

    transfer_token = get_token_for_resource_server(stored, TRANSFER_RESOURCE_SERVER)
    transfer_rt = (transfer_token or {}).get("refresh_token")
    if transfer_rt:
        new_transfer = refresh_token_for_resource(client, transfer_rt)
        if new_transfer is not None:
            refreshed = replace_other_token(
                refreshed, TRANSFER_RESOURCE_SERVER, new_transfer
            )
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
    if args.save_env and not args.cluster:
        raise RuntimeError("--save-env requires --cluster (it picks the .env variable name)")

    if args.cluster:
        settings = load_mcp_settings()
        args.olcf_collection_id = getattr(settings, f"{args.cluster}_globus_collection_id")
        args.session_domain = CLUSTER_SESSION_DOMAINS[args.cluster]
        args.client_id = settings.globus_native_app_client_id
    if args.token_file is None:
        # Per-cluster cache: the two enclaves use different SSO identities, so
        # sharing one file would clobber the other cluster's refresh token.
        suffix = f"_{args.cluster}" if args.cluster else ""
        args.token_file = Path.home() / ".globus" / f"olcf_tokens{suffix}.json"

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
        data_access_collection = (
            args.olcf_collection_id if args.data_access else None
        )
        auth_data = interactive_login(
            client,
            data_access_collection,
            prompt_login=args.prompt_login or args.force_login,
            session_domain=args.session_domain,
        )

    validate_auth_data(auth_data)
    auth_data = _normalize_token_expiry(auth_data)
    save_tokens(args.token_file, auth_data)

    transfer_token = require_token(auth_data, TRANSFER_RESOURCE_SERVER)
    expires_at = transfer_token.get("expires_at_seconds")

    print(f"Saved token data to {args.token_file}")
    if args.data_access:
        print(f"OLCF collection ID: {args.olcf_collection_id} (data_access requested)")
    else:
        print("Transfer-only scope requested (no data_access).")
    print(f"Globus client ID:   {args.client_id}")
    if expires_at:
        ttl = max(int(expires_at - time.time()), 0)
        print(f"Transfer access token valid for ~{ttl} seconds.")
    print(f"Transfer token scopes: {transfer_token.get('scope', '')}")

    if args.save_env:
        refresh_token = transfer_token.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No refresh token in the Transfer token response, cannot --save-env. "
                "Re-run with --force-login (a --refresh-only flow reuses the existing "
                "access token and may not return a refresh token)."
            )
        env_file = REPO_ROOT / ".env"
        env_var = f"VISTA_MCP_{args.cluster.upper()}_GLOBUS_REFRESH_TOKEN"
        update_env_file(env_file, env_var, refresh_token)
        print(f"Wrote {env_var} to {env_file}")
    if args.print_token:
        print("\nTransfer access token:")
        print(transfer_token["access_token"])
        print("\nRefresh token:")
        print(transfer_token["refresh_token"])


if __name__ == "__main__":
    main()
