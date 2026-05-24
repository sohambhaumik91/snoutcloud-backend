"""Nose-crop inference routes — kicks off the embedding pipeline."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.deps import get_current_user_id
from app.core.models import (
    EmbeddingJobIntent,
    EmbeddingJobStatus,
    InferenceStartRequest,
    InferenceStartResponse,
)
from app.db.client import get_supabase
from app.pipelines.nose_pipeline import run_pipeline

router = APIRouter(prefix="/inference", tags=["inference"])


@router.post("/start", response_model=InferenceStartResponse)
async def start_inference(
    payload: InferenceStartRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
) -> InferenceStartResponse:
    """Trigger the embedding pipeline for an already-uploaded set of crops."""
    sb = get_supabase()

    try:
        job_lookup = (
            sb.table("embedding_jobs")
            .select("id, user_id, intent, status")
            .eq("id", str(payload.embedding_job_id))
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to look up embedding job: {e}")

    if not job_lookup.data:
        raise HTTPException(status_code=404, detail="Embedding job not found")

    job = job_lookup.data[0]

    if job.get("user_id") != str(user_id):
        # don't leak existence — but the row was found, so 403 is honest here
        raise HTTPException(status_code=403, detail="You do not own this embedding job")

    if job.get("status") != EmbeddingJobStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Embedding job is not pending (current status: {job.get('status')})",
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        update_result = (
            sb.table("embedding_jobs")
            .update({
                "status": EmbeddingJobStatus.PROCESSING.value,
                "updated_at": now_iso,
            })
            .eq("id", str(payload.embedding_job_id))
            .eq("status", EmbeddingJobStatus.PENDING.value)  # CAS guard against races
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update job status: {e}")

    if not update_result.data:
        # someone else flipped status between SELECT and UPDATE
        raise HTTPException(
            status_code=409,
            detail="Embedding job status changed concurrently; refusing to start",
        )

    if job.get("intent") == EmbeddingJobIntent.ENROLLMENT.value:
        try:
            sb.table("registrations").update({
                "status": "processing",
                "updated_at": now_iso,
            }).eq("embedding_job_id", str(payload.embedding_job_id)).execute()
        except Exception as e:
            # registration update is best-effort — log via HTTPException would be
            # wrong since the job is already processing. Swallow and rely on
            # pipeline-side reconciliation.
            pass

    background_tasks.add_task(run_pipeline, payload.embedding_job_id)

    return InferenceStartResponse(
        embedding_job_id=payload.embedding_job_id,
        status=EmbeddingJobStatus.PROCESSING.value,
    )
