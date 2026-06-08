"""Dog routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user_id
from app.db.client import get_supabase

router = APIRouter(tags=["dogs"])


@router.get("/get-pack")
async def get_pack(user_id: UUID = Depends(get_current_user_id)) -> list[dict]:
    """Return all dogs owned by the authenticated user."""
    sb = get_supabase()
    try:
        res = (
            sb.table("dogs")
            .select("id, name, breed, sex, date_of_birth, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dogs: {e}")
    return res.data or []
