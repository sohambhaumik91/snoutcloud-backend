"""Re-scan routes for re-enrolling an existing dog's nose prints."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user_id
from app.api.routes.registration import _build_presigned_uploads
from app.core.models import (
    EmbeddingJobIntent,
    EmbeddingJobStatus,
    RescanStartRequest,
    RescanStartResponse,
)
from app.db.client import get_supabase

router = APIRouter(prefix="/rescan", tags=["rescan"])


@router.post("/start", response_model=RescanStartResponse)
async def start_rescan(
    payload: RescanStartRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> RescanStartResponse:
    """Initiate a re-upload of nose crops for an existing dog.

    No `registrations` row is created. Caller must own the dog.
    """
    sb = get_supabase()

    # ownership check — never trust the dog_id without verifying the JWT sub owns it.
    try:
        dog_lookup = (
            sb.table("dogs")
            .select("id, user_id")
            .eq("id", str(payload.dog_id))
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to look up dog: {e}")

    if not dog_lookup.data:
        raise HTTPException(status_code=404, detail="Dog not found")

    owner_id = dog_lookup.data[0].get("user_id")
    if owner_id != str(user_id):
        raise HTTPException(status_code=403, detail="You do not own this dog")

    job_id = uuid4()
    storage_path = f"nose-crops/{payload.dog_id}/{job_id}"
    now = datetime.now(timezone.utc)

    job_row = {
        "id": str(job_id),
        "user_id": str(user_id),
        "dog_id": str(payload.dog_id),
        "intent": EmbeddingJobIntent.RESCAN.value,
        "status": EmbeddingJobStatus.PENDING.value,
        "storage_path": storage_path,
        # embedding_version is bumped only when the rescan actually completes;
        # leave it at default here so a failed rescan does not skew the version.
        "embedding_version": 1,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        job_result = sb.table("embedding_jobs").insert(job_row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create embedding job: {e}")

    if not job_result.data:
        raise HTTPException(status_code=500, detail="Embedding job insert returned no rows")

    try:
        presigned_urls = _build_presigned_uploads(storage_path)
    except Exception as e:
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create presigned URLs: {e}")

    return RescanStartResponse(
        embedding_job_id=job_id,
        presigned_urls=presigned_urls,
    )
