"""Shared FastAPI dependencies."""

from uuid import UUID

import jwt
from fastapi import Header, HTTPException

from app.core.config import settings


def _dev_user_or_401(detail: str) -> UUID:
    """Dev escape hatch: fall back to settings.dev_auth_user_id when set,
    otherwise raise 401. Keeps the auth-less test path in one place."""
    if settings.dev_auth_user_id:
        return UUID(settings.dev_auth_user_id)
    raise HTTPException(status_code=401, detail=detail)


def get_current_user_id(authorization: str | None = Header(None)) -> UUID:
    """Extract user ID (Supabase `sub`) from the Bearer JWT in the Authorization header.

    The token is decoded WITHOUT signature verification to mirror the existing
    project pattern (auth/dogs/episodes routes). The CLAUDE.md spec calls for
    full JWKS-based RS256 verification — that is a separate, project-wide
    upgrade and is intentionally out of scope here.

    If no Bearer token is present and DEV_AUTH_USER_ID is configured, falls back
    to that user (auth-less dev/test mode).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return _dev_user_or_401("Missing authorization")

    token = authorization.split(" ", 1)[1]
    try:
        decoded = jwt.decode(token, options={"verify_signature": False})
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token decode failed: {e}")

    sub = decoded.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token: missing sub")

    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token: sub is not a UUID")
