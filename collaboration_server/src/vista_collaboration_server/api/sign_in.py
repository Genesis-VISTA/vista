"""
`GET /sign-in`: the page a researcher opens from VISTA to get a token to paste
back into it.

The server issues no tokens and runs no login. A login gateway in front of it
does: any proxy that logs a browser in with the configured realm and forwards
the request with `Authorization: Bearer <access token>` (Obsidian's APISIX on
VISTA's deployment). This page shows that token to its owner, once it has been
checked like any other request's. Only this route needs the gateway's browser
login; every other one takes the token alone.
"""

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..services.identity import User, bearer_token, get_user

router = APIRouter(tags=["meta"])

_HEADERS = {
    # The page holds a credential: never cached or framed, and runs nothing.
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


@router.get("/sign-in", response_class=HTMLResponse, include_in_schema=False)
async def sign_in_page(
    request: Request, user: User = Depends(get_user)
) -> HTMLResponse:
    # `get_user` has checked it; a dev-headers server, which takes none, has
    # no page to show (VISTA never links to it).
    token = bearer_token(request.headers.get("authorization")) or ""
    body = (
        f"<p>Signed in as <strong>{escape(user.name or user.email or user.sub)}"
        "</strong>. Copy this token into VISTA, under Settings → Collaboration. "
        "Treat it like a password; come back for a new one when it expires.</p>"
        "<textarea readonly rows='8' style='width:100%;font-family:monospace;"
        f"word-break:break-all'>{escape(token)}</textarea>"
    )
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>VISTA sign-in</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;"
        f"padding:0 1rem'><h1>VISTA sign-in</h1>{body}</body></html>",
        headers=_HEADERS,
    )
