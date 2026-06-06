"""Re-scan routes for re-enrolling an existing dog's nose prints."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user_id
from app.api.routes.registration import _build_presigned_uploads, frame_rows
from app.core.config import settings
from app.core.models import (
    EmbeddingJobIntent,
    EmbeddingJobStatus,
    RescanStartRequest,
    RescanStartResponse,
)
from app.db.client import get_supabase

router = APIRouter(prefix="/rescan", tags=["rescan"])


def _next_attempt_number(sb, dog_id: UUID) -> int:
    """attempt_number for this dog's next scan = max(existing) + 1.

    Enrollment is always attempt 1, so a dog's first rescan becomes attempt 2.
    """
    res = (
        sb.table("registrations")
        .select("attempt_number")
        .eq("dog_id", str(dog_id))
        .order("attempt_number", desc=True)
        .limit(1)
        .execute()
    )
    if res.data and res.data[0].get("attempt_number") is not None:
        return res.data[0]["attempt_number"] + 1
    return 1


@router.post("/start", response_model=RescanStartResponse)
async def start_rescan(
    payload: RescanStartRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> RescanStartResponse:
    """Initiate a re-upload of nose crops for an existing dog.

    A `registrations` row IS created (one row per scan attempt, tracked via
    `attempt_number`) so rescan frames hang off the same `nose_scan_frames`
    structure as enrollment. Caller must own the dog.
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
    registration_id = uuid4()
    # RLS on dog_nose_crops requires the first folder to be `private`
    storage_path = f"{settings.nose_crops_prefix}/nose-crops/{payload.dog_id}/{job_id}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.registration_ttl_seconds)
    attempt_number = _next_attempt_number(sb, payload.dog_id)

    # storage_path is not a column on embedding_jobs — see registration.py note.
    job_row = {
        "id": str(job_id),
        "user_id": str(user_id),
        "dog_id": str(payload.dog_id),
        "intent": EmbeddingJobIntent.RESCAN.value,
        "status": EmbeddingJobStatus.PENDING.value,
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

    # Scan-attempt row — the anchor that frames FK into. dog_id is known up front
    # for a rescan (unlike enrollment, where it's backfilled on completion).
    registration_row = {
        "registration_id": str(registration_id),
        "user_id": str(user_id),
        "dog_id": str(payload.dog_id),
        "embedding_job_id": str(job_id),
        "attempt_number": attempt_number,
        "status": "initiated",
        "expires_at": expires_at.isoformat(),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        reg_result = sb.table("registrations").insert(registration_row).execute()
    except Exception as e:
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create registration: {e}")

    if not reg_result.data:
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail="Registration insert returned no rows")

    # Bidirectional link: registration → job (above) and job → registration.
    try:
        sb.table("embedding_jobs").update(
            {"registration_id": str(registration_id)}
        ).eq("id", str(job_id)).execute()
    except Exception as e:
        sb.table("registrations").delete().eq("registration_id", str(registration_id)).execute()
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to link registration: {e}")

    try:
        presigned_urls = _build_presigned_uploads(storage_path)
    except Exception as e:
        sb.table("registrations").delete().eq("registration_id", str(registration_id)).execute()
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create presigned URLs: {e}")

    try:
        sb.table("nose_scan_frames").insert(
            frame_rows(registration_id, job_id, presigned_urls)
        ).execute()
    except Exception as e:
        # deleting the registration cascades its frames (FK ON DELETE CASCADE)
        sb.table("registrations").delete().eq("registration_id", str(registration_id)).execute()
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create scan frames: {e}")

    return RescanStartResponse(
        registration_id=registration_id,
        embedding_job_id=job_id,
        presigned_urls=presigned_urls,
    )
