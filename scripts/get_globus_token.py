#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14, <3.15"
# dependencies = [
#    "globus_sdk",
#    "fastmcp>=3.1.1",
#    "pydantic-settings>=2.0",
# ]
# ///
"""Acquire the Globus tokens Vista needs to drive HPC backends.

This one script covers both facilities, picked by ``--cluster``:

* **OLCF** (``--cluster odo`` / ``--cluster frontier``) — mints the **pair** of
  refresh tokens a cluster's file operations need, and ``--save-env`` writes
  both to ``.env`` (deployment-wide secrets the MCP server reads at startup):

  * ``VISTA_MCP_<CLUSTER>_GLOBUS_REFRESH_TOKEN`` — Transfer, for listing
    directories and making them.
  * ``VISTA_MCP_<CLUSTER>_GLOBUS_HTTPS_REFRESH_TOKEN`` — the collection itself,
    for reading and writing file contents over the Globus HTTPS interface.

  Two because Globus issues one refresh token per resource server, and the
  collection is its own. Both are needed: one alone finds an output directory
  it cannot open.

      ./scripts/get_globus_token.py --cluster odo --save-env
      ./scripts/get_globus_token.py --cluster frontier --save-env

* **NERSC** (``--cluster perlmutter``) — mints a Globus **IRI access token**
  (scope ``…/ed3e577d-…/iri_api``). On NERSC a single IRI token authorizes both
  compute (submit/status) and file ops, and it is a **per-user** credential, so
  the script prints it for you to paste into the Vista UI (the Perlmutter / NERSC
  IRI token field) rather than writing it to ``.env``:

      ./scripts/get_globus_token.py --cluster perlmutter

All flows use the Globus Native App device/auth-code flow against the same client
ID, request refresh tokens, and cache them under ``~/.globus/`` so re-running
refreshes silently instead of forcing another browser login.

The OLCF collections VISTA uses are Globus Connect Server 5 **high-assurance
mapped** collections. They reject a ``data_access`` scope — their session
requirement is what replaces it — so ``--data-access`` is off by default and
should stay off for Odo and Frontier. An earlier version of this file blamed the
resulting ``UNKNOWN_SCOPE_ERROR`` on "Globus 4 endpoints like the legacy OLCF
DTN"; that was wrong, and high assurance is the actual reason.

Ports the structure of NERSC's iri-api-get-globus-token and jqyin/OLCF-Globus-Transfer.
See https://github.com/NERSC/iri-api-get-globus-token and
https://github.com/jqyin/OLCF-Globus-Transfer/blob/main/get_olcf_token.py
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

# Same Native App client ID used by NERSC's iri-api-get-globus-token and the OLCF
# transfer helper; settings.globus_native_app_client_id resolves to the same value.
DEFAULT_CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"

# OLCF DTN (NCCS Open DTN) Globus collection UUID. Override with
# --olcf-collection-id if you are transferring from a different OLCF
# collection (e.g. an HPSS or project-specific GCS5 collection).
DEFAULT_OLCF_COLLECTION_ID = "ef1a9560-7ca1-11e5-992c-22000b96db58"

TRANSFER_RESOURCE_SERVER = "transfer.api.globus.org"
AUTH_RESOURCE_SERVER = "auth.globus.org"
TRANSFER_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"

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

# OLCF enclaves authenticate against different SSO domains; NERSC does not pin one.
CLUSTER_SESSION_DOMAINS = {
    "odo": "opensso.ccs.ornl.gov",
    "frontier": "sso.ccs.ornl.gov",
}
OLCF_CLUSTERS = frozenset(CLUSTER_SESSION_DOMAINS)
NERSC_CLUSTERS = frozenset({"perlmutter"})
ALL_CLUSTERS = sorted(OLCF_CLUSTERS | NERSC_CLUSTERS)


def facility_of(cluster: str | None) -> str:
    """Map a cluster to its facility ('nersc' or 'olcf'). No cluster => olcf default."""
    return "nersc" if cluster in NERSC_CLUSTERS else "olcf"


def primary_resource_server(facility: str) -> str:
    """The resource server whose token is the deliverable for this facility."""
    return NERSC_IRI_RESOURCE_SERVER if facility == "nersc" else TRANSFER_RESOURCE_SERVER


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


def https_scope(collection_id: str) -> str:
    """The per-collection scope that authorizes the Globus HTTPS interface.

    Not a dependent scope of Transfer's: the collection is its own resource
    server, so this comes back as its own token.
    """
    return f"https://auth.globus.org/scopes/{collection_id}/https"


def get_requested_scopes(
    facility: str,
    data_access_collection_id: str | None,
    https_collection_id: str | None = None,
) -> list[str]:
    base = sorted(REQUIRED_AUTH_SCOPES)
    if facility == "nersc":
        return base + [NERSC_IRI_SCOPE]
    scopes = base + [build_transfer_scope(data_access_collection_id)]
    if https_collection_id:
        scopes.append(https_scope(https_collection_id))
    return scopes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Get Globus tokens for Vista's HPC backends: an OLCF Transfer refresh "
            "token (odo/frontier file ops) or a NERSC IRI access token (perlmutter). "
            "Tokens are cached to a secure local file."
        )
    )
    parser.add_argument(
        "--cluster",
        choices=ALL_CLUSTERS,
        default=None,
        help=(
            "Cluster to mint a token for. OLCF (odo/frontier) -> Transfer refresh "
            "token; NERSC (perlmutter) -> IRI access token. Pulls the client ID "
            "(and, for OLCF, the collection ID + SSO session domain) from the "
            "vista_mcp_server config."
        ),
    )
    parser.add_argument(
        "--save-env",
        action="store_true",
        help=(
            "OLCF only: write both refresh tokens to .env at the repo root, as "
            "VISTA_MCP_<CLUSTER>_GLOBUS_REFRESH_TOKEN and "
            "VISTA_MCP_<CLUSTER>_GLOBUS_HTTPS_REFRESH_TOKEN (requires --cluster). "
            "NERSC IRI tokens are per-user; paste the printed access token into the UI."
        ),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help=(
            "Path for saved token JSON (default: ~/.globus/<facility>_tokens_<cluster>.json)"
        ),
    )
    parser.add_argument(
        "--olcf-collection-id",
        default=DEFAULT_OLCF_COLLECTION_ID,
        help=(
            "OLCF only: UUID of the Globus collection you will transfer from "
            f"(default: {DEFAULT_OLCF_COLLECTION_ID}). Only used with --data-access."
        ),
    )
    parser.add_argument(
        "--data-access",
        action="store_true",
        help=(
            "OLCF only: also request the data_access dependent scope for "
            "--olcf-collection-id. Do NOT set this for Odo or Frontier: their "
            "collections are high-assurance mapped collections, which reject "
            "data_access (the whole login then fails with UNKNOWN_SCOPE_ERROR). "
            "Here for a non-high-assurance GCS5 collection that needs it."
        ),
    )
    parser.add_argument(
        "--session-domain",
        default=None,
        help=(
            "Force the authorize URL to require an identity from this domain "
            "(session_required_single_domain). Set automatically per OLCF cluster; "
            "left unset for NERSC."
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
        help="Also print the refresh token (the primary access token is printed for NERSC).",
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


def validate_auth_data(
    auth_data: dict, primary_resource: str, https_resource: str | None = None
) -> None:
    auth_token = require_token(auth_data, AUTH_RESOURCE_SERVER)
    granted = parse_scope_string(auth_token.get("scope", ""))
    missing = REQUIRED_AUTH_SCOPES - granted
    if missing:
        raise RuntimeError(f"Missing required Globus Auth scopes: {sorted(missing)}")
    require_token(auth_data, primary_resource)
    if https_resource:
        # Required, not optional: half a credential reads as connected and then
        # fails on the first file. A cache minted before the HTTPS scope was
        # added has only the Transfer half, and this is what sends it back
        # through a login rather than saving it.
        require_token(auth_data, https_resource)


def interactive_login(
    client: globus_sdk.NativeAppAuthClient,
    facility: str,
    data_access_collection_id: str | None,
    *,
    https_collection_id: str | None = None,
    prompt_login: bool = False,
    session_domain: str | None = None,
) -> dict:
    client.oauth2_start_flow(
        requested_scopes=" ".join(
            get_requested_scopes(
                facility, data_access_collection_id, https_collection_id
            )
        ),
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


def set_token(stored: dict, resource_server: str, refreshed: dict) -> dict:
    """Write `refreshed` back into `stored` wherever that resource server lives.

    A token can be either the top-level token or one of `other_tokens`, depending
    on which scope Globus chose as the response's primary. Handle both so refresh
    works regardless of facility/scope ordering.
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
    client: globus_sdk.NativeAppAuthClient,
    stored: dict,
    primary_resource: str,
    https_resource: str | None = None,
) -> dict | None:
    refreshed = dict(stored)
    any_refreshed = False

    wanted = [AUTH_RESOURCE_SERVER, primary_resource]
    if https_resource:
        wanted.append(https_resource)
    for resource_server in wanted:
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
        validate_auth_data(refreshed, primary_resource, https_resource)
    except RuntimeError:
        return None
    return refreshed


def main() -> None:
    args = parse_args()
    if args.force_login and args.refresh_only:
        raise RuntimeError("Choose only one of --force-login or --refresh-only")

    facility = facility_of(args.cluster)
    primary_resource = primary_resource_server(facility)

    if args.save_env and not args.cluster:
        raise RuntimeError("--save-env requires --cluster (it picks the .env variable name)")
    if args.save_env and facility == "nersc":
        raise RuntimeError(
            "--save-env is OLCF-only. NERSC IRI tokens are per-user — paste the "
            "access token this prints into the Vista UI (Perlmutter / NERSC IRI "
            "token field), not .env."
        )

    https_resource: str | None = None
    if args.cluster:
        settings = load_mcp_settings()
        args.client_id = settings.globus_native_app_client_id
        if facility == "olcf":
            args.olcf_collection_id = getattr(settings, f"{args.cluster}_globus_collection_id")
            args.session_domain = CLUSTER_SESSION_DOMAINS[args.cluster]
            # The collection is its own resource server, so its token comes back
            # under the collection UUID rather than under Transfer.
            https_resource = args.olcf_collection_id
    if args.token_file is None:
        # Per-cluster cache: different facilities/enclaves use different identities,
        # so sharing one file would clobber another cluster's refresh token.
        suffix = f"_{args.cluster}" if args.cluster else ""
        args.token_file = Path.home() / ".globus" / f"{facility}_tokens{suffix}.json"

    client = globus_sdk.NativeAppAuthClient(args.client_id)

    auth_data = None
    if not args.force_login:
        stored = load_tokens(args.token_file)
        if stored:
            auth_data = refresh_stored_tokens(
                client, stored, primary_resource, https_resource
            )

    if auth_data is None:
        if args.refresh_only:
            raise RuntimeError(
                "Refresh-only mode failed. No usable saved refresh token was "
                "found, or refresh did not return all required tokens."
            )
        data_access_collection = (
            args.olcf_collection_id if (facility == "olcf" and args.data_access) else None
        )
        auth_data = interactive_login(
            client,
            facility,
            data_access_collection,
            https_collection_id=https_resource,
            prompt_login=args.prompt_login or args.force_login,
            session_domain=args.session_domain,
        )

    validate_auth_data(auth_data, primary_resource, https_resource)
    auth_data = _normalize_token_expiry(auth_data)
    save_tokens(args.token_file, auth_data)

    primary = require_token(auth_data, primary_resource)
    expires_at = primary.get("expires_at_seconds")

    print(f"Saved token data to {args.token_file}")
    print(f"Globus client ID:   {args.client_id}")
    if expires_at:
        ttl = max(int(expires_at - time.time()), 0)
        print(f"Access token valid for ~{ttl} seconds.")
    print(f"Primary token scopes: {primary.get('scope', '')}")

    if facility == "nersc":
        print("\n=== NERSC IRI access token ===")
        print(primary["access_token"])
        print(
            "\nPaste the token above into the Vista UI as your Perlmutter / NERSC "
            "IRI token. Re-run this script to mint a fresh one (it refreshes silently)."
        )
        if args.print_token and primary.get("refresh_token"):
            print("\nRefresh token (cached in the token file for silent refresh):")
            print(primary["refresh_token"])
        return

    # --- OLCF: the deliverable is the pair of refresh tokens for .env ---
    print(f"OLCF collection ID: {args.olcf_collection_id}")
    if args.data_access:
        print("  data_access requested (not valid for Odo or Frontier).")
    if https_resource:
        print("  HTTPS interface scope requested (no data_access).")

    https_token = (
        get_token_for_resource_server(auth_data, https_resource) or {}
        if https_resource
        else {}
    )

    if args.save_env:
        env_file = REPO_ROOT / ".env"
        prefix = f"VISTA_MCP_{args.cluster.upper()}_GLOBUS"
        for token, suffix, what in (
            (primary, "REFRESH_TOKEN", "Transfer"),
            (https_token, "HTTPS_REFRESH_TOKEN", "the collection's HTTPS interface"),
        ):
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                raise RuntimeError(
                    f"No refresh token for {what}, cannot --save-env. Re-run with "
                    "--force-login (a --refresh-only flow reuses the existing "
                    "access token and may not return a refresh token)."
                )
            env_var = f"{prefix}_{suffix}"
            update_env_file(env_file, env_var, refresh_token)
            print(f"Wrote {env_var} to {env_file}")
    if args.print_token:
        print("\nTransfer access token:")
        print(primary["access_token"])
        print("\nTransfer refresh token:")
        print(primary["refresh_token"])
        if https_token:
            print("\nCollection (HTTPS) refresh token:")
            print(https_token.get("refresh_token"))


if __name__ == "__main__":
    main()
