"""
Check a deployed collaboration server end to end, through its login gateway,
as a researcher would reach it. Manual only, never in CI: it asks for a real
token, and leaves a `smoke-test` item on the server.

    cd collaboration_server
    uv run --extra dev python scripts/smoke.py [https://obsidian.ccs.ornl.gov/vista/api]

Asks for the token from the server's `/sign-in` page (as VISTA's Settings
does), then: `/version`, `/me`, a blob upload, a new item naming it, and
reading both back.
"""

import getpass
import hashlib
import sys
import time

import httpx

DEFAULT_URL = "https://obsidian.ccs.ornl.gov/vista/api"


def step(name: str, response: httpx.Response, *, json: bool = True) -> dict:
    ok = response.is_success
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {response.status_code}")
    if not ok:
        print(f"     {response.text[:500]}")
        sys.exit(1)
    return response.json() if json and response.content else {}


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).rstrip("/")
    print(f"Open {url}/sign-in in a browser, log in, and copy the token it shows.")
    token = getpass.getpass("Token: ").strip()

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=url, headers=headers, timeout=60.0) as http:
        version = step("GET /version", http.get("/version"))
        print(f"     server {version['server_version']}, API {version['api_version']}")
        me = step("GET /me", http.get("/me"))
        print(f"     signed in as {me['name']} <{me['email']}> ({me['sub']})")

        body = f"VISTA smoke test {time.time()}".encode()
        blob = step("POST /blobs/stream", http.post("/blobs/stream", content=body))
        assert blob["blob_ref"] == hashlib.sha256(body).hexdigest()

        item = step(
            "POST /items",
            http.post(
                "/items",
                json={
                    "kind": "smoke-test",
                    "name": f"smoke test by {me['name'] or me['sub']}",
                    "manifest": {"at": time.time()},
                    "blob_ref": blob["blob_ref"],
                },
            ),
        )
        assert item["created_by"] == me["sub"]
        got = http.get(f"/items/{item['id']}/versions/1/blob")
        step("GET the blob back", got, json=False)
        assert got.content == body

        anonymous = httpx.get(f"{url}/items", timeout=30.0)
        refused = anonymous.status_code == 401
        print(
            f"{'ok  ' if refused else 'FAIL'} without a token: "
            f"{anonymous.status_code} (expect 401)"
        )
        if not refused:
            sys.exit(1)
    print("All good.")


if __name__ == "__main__":
    main()
