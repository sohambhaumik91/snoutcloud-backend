"""Nose-crop inference routes — kicks off the embedding pipeline,
streams status via SSE, exposes a polling fallback, and handles
human-in-the-loop duplicate resolution."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.models import (
    DuplicateCandidate,
    EmbeddingJobStatus,
    InferenceStartRequest,
    InferenceStartResponse,
    ResolveRequest,
)
from app.db.client import get_supabase
from app.pipelines.nose_pipeline import inference_channel, _query_candidates
from app.services.queue import enqueue_inference
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inference", tags=["inference"])

SSE_HEARTBEAT_SECONDS = 15


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _decode_token_unverified(token: str) -> dict:
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token decode failed: {exc}")


def _job_for_user(embedding_job_id: UUID, user_id: str) -> dict:
    """Fetch an embedding job and 403 if the caller doesn't own it.

    Includes `pending_embedding` so SSE fast-path and /result can surface
    candidates without a second DB round-trip.
    """
    sb = get_supabase()
    try:
        res = (
            sb.table("embedding_jobs")
            .select(
                "id, user_id, dog_id, intent, status, duplicate_found, "
                "duplicate_dog_id, match_score, quality_passed, quality_notes, "
                "embedding_version, completed_at, pending_embedding"
            )
            .eq("id", str(embedding_job_id))
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to look up embedding job: {e}")
    if not res.data:
        raise HTTPException(status_code=404, detail="Embedding job not found")
    row = res.data[0]
    if row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You do not own this embedding job")
    return row


def _get_reg_for_job(embedding_job_id: UUID) -> dict | None:
    """Fetch the registrations row linked to this embedding job."""
    sb = get_supabase()
    try:
        res = (
            sb.table("registrations")
            .select("registration_id, status, dog_id")
            .eq("embedding_job_id", str(embedding_job_id))
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    data = res.data or []
    return data[0] if data else None


def _candidates_from_job(job: dict) -> list[dict]:
    """Re-run top-N pgvector search using the job's pending_embedding."""
    pending = job.get("pending_embedding")
    if not pending:
        return []
    # Supabase may return vector as string or list
    if isinstance(pending, str):
        try:
            pending = json.loads(pending)
        except Exception:
            return []
    sb = get_supabase()
    return _query_candidates(
        sb,
        pending,
        settings.duplicate_suggest_threshold,
        settings.duplicate_candidate_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /inference/start
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/start", response_model=InferenceStartResponse)
async def start_inference(
    payload: InferenceStartRequest,
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
            .update({"status": EmbeddingJobStatus.PROCESSING.value, "updated_at": now_iso})
            .eq("id", str(payload.embedding_job_id))
            .eq("status", EmbeddingJobStatus.PENDING.value)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update job status: {e}")

    if not update_result.data:
        raise HTTPException(
            status_code=409,
            detail="Embedding job status changed concurrently; refusing to start",
        )

    try:
        sb.table("registrations").update({
            "status": "processing",
            "updated_at": now_iso,
        }).eq("embedding_job_id", str(payload.embedding_job_id)).execute()
    except Exception:
        pass

    try:
        await enqueue_inference(payload.embedding_job_id)
    except Exception as e:
        sb.table("embedding_jobs").update({
            "status": EmbeddingJobStatus.PENDING.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", str(payload.embedding_job_id)).execute()
        raise HTTPException(status_code=500, detail=f"Failed to enqueue inference job: {e}")

    return InferenceStartResponse(
        embedding_job_id=payload.embedding_job_id,
        status=EmbeddingJobStatus.PROCESSING.value,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /inference/status/{id}  — SSE stream
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status/{embedding_job_id}")
async def status_stream(
    embedding_job_id: UUID,
    request: Request,
    token: str | None = None,
) -> StreamingResponse:
    """SSE stream of pipeline status.

    EventSource in React Native cannot set custom headers, so the JWT is passed
    as ?token= query param. The handler ownership-checks the job, flushes current
    DB state as the first event, then subscribes to the Redis pub/sub channel.

    Terminal events: complete, error, review_required (all close the stream).
    """
    if not token:
        if not settings.dev_auth_user_id:
            raise HTTPException(status_code=401, detail="Missing token")
        sub = settings.dev_auth_user_id
    else:
        decoded = _decode_token_unverified(token)
        sub = decoded.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token: missing sub")

    row = _job_for_user(embedding_job_id, sub)
    initial_status = row["status"]
    channel = inference_channel(embedding_job_id)

    # For the fast-path (job already finished), determine the right terminal event
    # before entering the generator so we avoid async calls inside the sync block.
    fast_event: tuple[str, dict] | None = None
    if initial_status in (EmbeddingJobStatus.COMPLETE.value, EmbeddingJobStatus.FAILED.value):
        if initial_status == EmbeddingJobStatus.COMPLETE.value:
            reg = _get_reg_for_job(embedding_job_id)
            if reg and reg.get("status") == "possible_duplicate":
                candidates = _candidates_from_job(row)
                fast_event = ("review_required", {"status": "possible_duplicate", "candidates": candidates})
            else:
                fast_event = ("complete", {
                    "status": "complete",
                    "duplicate_found": row.get("duplicate_found"),
                    "duplicate_dog_id": row.get("duplicate_dog_id"),
                    "match_score": row.get("match_score"),
                    "embedding_version": row.get("embedding_version"),
                    "dog_id": row.get("dog_id"),
                })
        else:
            fast_event = ("error", {
                "status": "failed",
                "reason": "job ended in failed state before stream attached",
            })

    async def event_generator():
        if fast_event is not None:
            yield _sse(fast_event[0], fast_event[1])
            return

        r = get_redis()
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(channel)
            yield _sse("status", {"status": initial_status, "step": "starting"})

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.CancelledError:
                    break
                if msg is None:
                    yield ": ping\n\n"
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    payload = json.loads(msg["data"])
                except (ValueError, TypeError):
                    logger.warning("malformed pubsub payload on %s: %r", channel, msg.get("data"))
                    continue
                etype = payload.get("type", "status")
                edata = payload.get("data", {})
                yield _sse(etype, edata)
                if etype in ("complete", "error", "review_required"):
                    break
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /inference/result/{id}  — polling fallback
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/result/{embedding_job_id}")
async def get_result(
    embedding_job_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Polling fallback for clients that drop the SSE connection.

    When registration is `possible_duplicate`, includes a fresh `candidates`
    list so the client can render the resolution screen without SSE.
    """
    row = _job_for_user(embedding_job_id, str(user_id))
    reg = _get_reg_for_job(embedding_job_id)
    reg_status = reg.get("status") if reg else None

    result: dict = {
        "embedding_job_id": str(embedding_job_id),
        "status": row.get("status"),
        "registration_status": reg_status,
        "intent": row.get("intent"),
        "dog_id": row.get("dog_id"),
        "duplicate_found": row.get("duplicate_found"),
        "duplicate_dog_id": row.get("duplicate_dog_id"),
        "match_score": row.get("match_score"),
        "quality_passed": row.get("quality_passed"),
        "quality_notes": row.get("quality_notes"),
        "embedding_version": row.get("embedding_version"),
        "completed_at": row.get("completed_at"),
    }

    if reg_status == "possible_duplicate":
        result["candidates"] = _candidates_from_job(row)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST /inference/{id}/resolve  — human-in-the-loop resolution
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{embedding_job_id}/resolve")
async def resolve_enrollment(
    embedding_job_id: UUID,
    payload: ResolveRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Commit the human's decision on an enrollment that is awaiting review.

    decision="new"      → create dog + active embedding from pending_embedding.
    decision="existing" → store pending_embedding as is_active=False under the
                          matched dog (preserved for future re-activation).

    Uses an atomic UPDATE … WHERE status='possible_duplicate' RETURNING * as the
    concurrency guard — Postgres MVCC ensures only one concurrent resolve wins.
    """
    if payload.decision not in ("new", "existing"):
        raise HTTPException(status_code=422, detail="decision must be 'new' or 'existing'")
    if payload.decision == "existing" and not payload.dog_id:
        raise HTTPException(status_code=422, detail="dog_id required when decision is 'existing'")

    sb = get_supabase()
    job = _job_for_user(embedding_job_id, str(user_id))

    if job.get("status") != EmbeddingJobStatus.COMPLETE.value:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not complete (status: {job.get('status')})",
        )

    pending = job.get("pending_embedding")
    if not pending:
        raise HTTPException(status_code=400, detail="No pending embedding — already resolved")

    # Handle vector returned as string by some Supabase client versions
    if isinstance(pending, str):
        try:
            pending = json.loads(pending)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to parse pending_embedding")

    # Fetch the linked registration
    reg = _get_reg_for_job(embedding_job_id)
    if not reg:
        raise HTTPException(status_code=404, detail="No registration found for this job")
    if reg["status"] != "possible_duplicate":
        raise HTTPException(
            status_code=400,
            detail=f"Registration is not awaiting resolution (status: {reg['status']})",
        )

    reg_id = reg["registration_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    new_reg_status = "completed" if payload.decision == "new" else "duplicate_confirmed"

    # Atomic guard: claim the registration row.
    # Only one concurrent resolve wins; the other sees 0 rows → 409.
    guard = (
        sb.table("registrations")
        .update({"status": new_reg_status, "updated_at": now_iso})
        .eq("registration_id", str(reg_id))
        .eq("status", "possible_duplicate")
        .execute()
    )
    if not guard.data:
        raise HTTPException(status_code=409, detail="already_resolved")

    if payload.decision == "new":
        new_dog_id = str(uuid4())
        sb.table("dogs").insert({
            "id": new_dog_id,
            "user_id": str(user_id),
            "name": "Unnamed",
            "sex": "unknown",
            "created_at": now_iso,
        }).execute()

        sb.table("dog_embeddings").insert({
            "dog_id": new_dog_id,
            "embedding_job_id": str(embedding_job_id),
            "embedding": pending,
            "is_active": True,
            "created_at": now_iso,
        }).execute()

        sb.table("embedding_jobs").update({
            "dog_id": new_dog_id,
            "pending_embedding": None,
            "updated_at": now_iso,
        }).eq("id", str(embedding_job_id)).execute()

        sb.table("registrations").update({
            "dog_id": new_dog_id,
            "updated_at": now_iso,
        }).eq("registration_id", str(reg_id)).execute()

        return {
            "embedding_job_id": str(embedding_job_id),
            "status": EmbeddingJobStatus.COMPLETE.value,
            "registration_status": "completed",
            "dog_id": new_dog_id,
            "embedding_version": job.get("embedding_version"),
        }

    # decision == "existing"
    dog_id = str(payload.dog_id)

    # Store the pending embedding as inactive under the matched dog.
    # Preserved for future re-activation once the encoder is validated.
    sb.table("dog_embeddings").insert({
        "dog_id": dog_id,
        "embedding_job_id": str(embedding_job_id),
        "embedding": pending,
        "is_active": False,
        "created_at": now_iso,
    }).execute()

    sb.table("embedding_jobs").update({
        "duplicate_dog_id": dog_id,
        "pending_embedding": None,
        "updated_at": now_iso,
    }).eq("id", str(embedding_job_id)).execute()

    sb.table("registrations").update({
        "dog_id": dog_id,
        "updated_at": now_iso,
    }).eq("registration_id", str(reg_id)).execute()

    return {
        "embedding_job_id": str(embedding_job_id),
        "status": EmbeddingJobStatus.COMPLETE.value,
        "registration_status": "duplicate_confirmed",
        "dog_id": dog_id,
        "embedding_version": job.get("embedding_version"),
    }
