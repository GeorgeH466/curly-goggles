"""Local-only access guard.

The dashboard listens on the loopback interface, but browsers will happily let
any website send requests to ``http://127.0.0.1:<port>``.  This middleware
blocks DNS-rebinding (unknown ``Host``) and cross-site requests (mismatched
``Origin``) so a random web page cannot shut down your PC or read your files.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.websockets import WebSocket

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def allowed_hosts() -> set[str]:
    extra = {
        host.strip().lower()
        for host in os.environ.get("DASHBOARD_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    return LOCAL_HOSTS | extra


def split_host(value: str) -> str:
    """Extract the hostname from ``host:port`` or ``[::1]:port``."""
    if not value:
        return ""
    if value.startswith("["):
        return value[1: value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def host_ok(host: str) -> bool:
    host = (host or "").lower()
    if host in allowed_hosts():
        return True
    # Opt-in support for hosted preview proxies (e.g. sandbox domains).
    suffix = os.environ.get("DASHBOARD_ALLOWED_SUFFIX", ".e2b.app")
    return bool(suffix) and host.endswith(suffix)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host_header = request.headers.get("host", "")
        bare_host = split_host(host_header)
        if bare_host and not host_ok(bare_host):
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)

        origin = request.headers.get("origin")
        if origin and origin != "null":
            origin_host = split_host(origin.split("://", 1)[-1].rstrip("/"))
            if not host_ok(origin_host) or (bare_host and origin_host != bare_host):
                return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        return await call_next(request)


def websocket_origin_ok(ws: WebSocket) -> bool:
    """Validate the Origin header of a websocket handshake."""
    origin = ws.headers.get("origin")
    host = split_host(ws.headers.get("host", ""))
    if not origin:
        return True  # non-browser client (curl, tests, CLI tools)
    origin_host = split_host(origin.split("://", 1)[-1].rstrip("/"))
    return host_ok(origin_host) and (not host or origin_host == host)
