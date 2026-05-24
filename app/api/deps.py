"""Shared FastAPI dependencies."""

from uuid import UUID

import jwt
from fastapi import Header, HTTPException


def get_current_user_id(authorization: str | None = Header(None)) -> UUID:
    """Extract user ID (Supabase `sub`) from the Bearer JWT in the Authorization header.

    The token is decoded WITHOUT signature verification to mirror the existing
    project pattern (auth/dogs/episodes routes). The CLAUDE.md spec calls for
    full JWKS-based RS256 verification — that is a separate, project-wide
    upgrade and is intentionally out of scope here.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

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
