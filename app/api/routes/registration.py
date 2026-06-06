"""Dog enrollment / registration routes."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.models import (
    EmbeddingJobIntent,
    EmbeddingJobStatus,
    PresignedUpload,
    RegistrationStartResponse,
)
from app.db.client import get_supabase
from app.services.storage import create_signed_upload_url

router = APIRouter(prefix="/registration", tags=["registration"])

CROP_COUNT = 8


def _build_presigned_uploads(prefix: str) -> list[PresignedUpload]:
    """Generate 8 presigned upload URLs for crop_1.jpg .. crop_8.jpg under `prefix`."""
    uploads: list[PresignedUpload] = []
    for i in range(1, CROP_COUNT + 1):
        path = f"{prefix}/crop_{i}.jpg"
        signed = create_signed_upload_url(path, settings.nose_crops_bucket)
        uploads.append(
            PresignedUpload(
                index=i,
                url=signed["signed_url"],
                path=signed["path"],
                token=signed["token"],
            )
        )
    return uploads


def frame_rows(registration_id: UUID, job_id: UUID,
               uploads: list[PresignedUpload]) -> list[dict]:
    """One nose_scan_frames row per crop. `frame_index` is 0-based (the DB
    CHECK is 0..11), so it is the client-facing `index` (1..8) minus one.
    `status` and `selected_for_embed` fall back to their column defaults
    (`uploaded` / false)."""
    return [
        {
            "registration_id": str(registration_id),
            "job_id": str(job_id),
            "frame_index": u.index - 1,
            "storage_path": u.path,
        }
        for u in uploads
    ]


@router.post("/start", response_model=RegistrationStartResponse)
async def start_registration(
    user_id: UUID = Depends(get_current_user_id),
) -> RegistrationStartResponse:
    """Initiate a new dog enrollment.

    Creates an `embedding_jobs` row (intent=enrollment) and a `registrations`
    row, then returns 8 presigned URLs the client uses to upload nose crops
    directly to Supabase Storage.
    """
    sb = get_supabase()

    job_id = uuid4()
    registration_id = uuid4()
    # RLS on dog_nose_crops requires the first folder to be `private`
    storage_path = f"{settings.nose_crops_prefix}/nose-crops/pending/{job_id}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.registration_ttl_seconds)

    # NB: storage_path is NOT stored on embedding_jobs (no such column). The
    # per-crop full path lives on each nose_scan_frames row; `storage_path` here
    # is just the local prefix used to build the presigned URLs + frame rows.
    job_row = {
        "id": str(job_id),
        "user_id": str(user_id),
        "intent": EmbeddingJobIntent.ENROLLMENT.value,
        "status": EmbeddingJobStatus.PENDING.value,
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

    registration_row = {
        "registration_id": str(registration_id),
        "user_id": str(user_id),
        "embedding_job_id": str(job_id),
        "attempt_number": 1,
        "status": "initiated",
        "expires_at": expires_at.isoformat(),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        reg_result = sb.table("registrations").insert(registration_row).execute()
    except Exception as e:
        # rollback the orphan embedding_jobs row so we don't leak state
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create registration: {e}")

    if not reg_result.data:
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail="Registration insert returned no rows")

    # Bidirectional link: registration → job (above) and job → registration, so
    # both flows (enrollment + rescan) carry the same job↔registration linkage.
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
        # rollback both rows — there is nothing for the client to do without URLs
        sb.table("registrations").delete().eq("registration_id", str(registration_id)).execute()
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create presigned URLs: {e}")

    try:
        sb.table("nose_scan_frames").insert(
            frame_rows(registration_id, job_id, presigned_urls)
        ).execute()
    except Exception as e:
        # rollback: deleting the registration cascades its frames (FK ON DELETE
        # CASCADE); then drop the job.
        sb.table("registrations").delete().eq("registration_id", str(registration_id)).execute()
        sb.table("embedding_jobs").delete().eq("id", str(job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to create scan frames: {e}")

    return RegistrationStartResponse(
        registration_id=registration_id,
        embedding_job_id=job_id,
        presigned_urls=presigned_urls,
        expires_at=expires_at,
    )
