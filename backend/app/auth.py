"""Simple auth: one shared password (from a Kubernetes Secret) exchanged for
an HMAC-signed, expiring bearer token. Good enough for a homelab dashboard.

If PIWATCH_PASSWORD is unset, auth is disabled (e.g. local demo mode).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

TOKEN_TTL = int(os.environ.get("PIWATCH_TOKEN_TTL", str(12 * 3600)))

_PASSWORD = os.environ.get("PIWATCH_PASSWORD", "")
# Secret used for signing; derived from password if not given explicitly.
# NOTE: with 2 replicas both must sign identically -- both mount the same
# Secret, so tokens issued by replica A validate on replica B (failover!).
_SECRET = (
    os.environ.get("PIWATCH_SECRET")
    or hashlib.sha256(("piwatch:" + _PASSWORD).encode()).hexdigest()
).encode()


def auth_enabled() -> bool:
    return bool(_PASSWORD)


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def create_token() -> str:
    expiry = str(int(time.time()) + TOKEN_TTL)
    payload = base64.urlsafe_b64encode(expiry.encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), sig):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        expiry = int(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        return False
    return time.time() < expiry


# ---------------- FastAPI wiring ----------------

router = APIRouter()


class LoginRequest(BaseModel):
    password: str


@router.post("/api/login")
def login(body: LoginRequest):
    if not auth_enabled():
        return {"token": "", "auth": False}
    if not secrets.compare_digest(body.password, _PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": create_token(), "auth": True, "ttl": TOKEN_TTL}


@router.get("/api/auth")
def auth_info():
    return {"auth": auth_enabled()}


def require_auth(authorization: str | None = Header(default=None)):
    """Dependency for REST endpoints."""
    if not auth_enabled():
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")


def ws_token_ok(token: str | None = Query(default=None)) -> bool:
    """WebSocket auth: token passed as ?token= query parameter."""
    return verify_token(token)
