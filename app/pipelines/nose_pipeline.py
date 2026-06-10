"""Nose-crop embedding pipeline.

End-to-end flow per snoutcloud-backend/CLAUDE.md:

  fetch crops  →  quality gating  →  embed (ConvNeXt+GeM)
              →  candidate search (enrollment only, pgvector)
              →  stash pending_embedding + emit review_required (enrollment)
              →  persist active embedding (rescan)

Publishes step events to Redis pub/sub channel `inference:{job_id}` so the SSE
endpoint in app/api/routes/inference.py can stream progress to the client.

Enrollment NEVER auto-creates a dog. The worker always emits `review_required`
with a (possibly empty) candidate list and waits for the human tap on
POST /inference/{job_id}/resolve.
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import torch
import torch.nn.functional as F

from app.core.config import settings
from app.core.models import EmbeddingJobIntent, EmbeddingJobStatus, FrameStatus
from app.db.client import get_supabase
from app.ml.encoder import embed_batch
from app.ml.preprocess import preprocess_for_inference, quality_check
from app.services.redis_client import publish

logger = logging.getLogger(__name__)

# min crops that must pass quality gating (of 8); tunable via QUALITY_MIN_PASSING_CROPS
MIN_PASSING_CROPS = settings.quality_min_passing_crops


def inference_channel(embedding_job_id: UUID | str) -> str:
    return f"inference:{embedding_job_id}"


async def _emit_status(job_id: UUID, step: str) -> None:
    await publish(
        inference_channel(job_id),
        {"type": "status", "data": {"status": "processing", "step": step}},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Storage I/O (sync — wrapped in asyncio.to_thread when called)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_frames(sb, job_id: UUID) -> list[dict]:
    """Load this job's nose_scan_frames rows and download each crop.

    `nose_scan_frames` (not a storage listing) is the source of truth for which
    crops belong to a job — `job_id` is the universal key across enrollment and
    rescan. Returns dicts {frame_id, frame_index, blob} ordered by frame_index;
    `blob` is None if the download failed (treated as a quality failure later).
    """
    try:
        res = (
            sb.table("nose_scan_frames")
            .select("frame_id, frame_index, storage_path")
            .eq("job_id", str(job_id))
            .order("frame_index")
            .execute()
        )
    except Exception:
        logger.exception("failed to query nose_scan_frames for job %s", job_id)
        return []

    bucket = settings.nose_crops_bucket
    frames: list[dict] = []
    for row in res.data or []:
        path = row["storage_path"]
        try:
            blob = sb.storage.from_(bucket).download(path)
        except Exception:
            logger.exception("failed to download %s", path)
            blob = None
        frames.append({
            "frame_id": row["frame_id"],
            "frame_index": row["frame_index"],
            "blob": blob,
        })
    return frames


def _mark_frame(sb, frame_id: str, selected: bool, status: str, ml_metadata: dict) -> None:
    """Persist a frame's quality outcome. The updated_at trigger handles the timestamp."""
    sb.table("nose_scan_frames").update({
        "selected_for_embed": selected,
        "status": status,
        "ml_metadata": ml_metadata,
    }).eq("frame_id", str(frame_id)).execute()


def _query_candidates(sb, embedding: list[float], threshold: float, count: int) -> list[dict]:
    """Call match_nose_embedding RPC; return enriched candidate list.

    Each candidate: {dog_id, name, breed, match_score}.
    Candidates are ordered by similarity (highest first, from the RPC).
    An empty list means no matches above threshold — not an error.
    """
    try:
        res = sb.rpc(
            "match_nose_embedding",
            {
                "query_embedding": embedding,
                "match_threshold": threshold,
                "match_count": count,
            },
        ).execute()
    except Exception:
        logger.exception("match_nose_embedding RPC failed; returning no candidates")
        return []

    rows = res.data or []
    if not rows:
        return []

    # Enrich with dog name/breed and main profile photo via single IN queries
    dog_ids = [r["dog_id"] for r in rows if r.get("dog_id")]
    dogs_by_id: dict[str, dict] = {}
    photos_by_dog: dict[str, str] = {}
    if dog_ids:
        try:
            dogs_res = sb.table("dogs").select("id, name, breed").in_("id", dog_ids).execute()
            for d in (dogs_res.data or []):
                dogs_by_id[d["id"]] = d
        except Exception:
            logger.exception("failed to enrich candidates with dog name/breed")
        try:
            photos_res = (
                sb.table("dog_photos")
                .select("dog_id, storage_path")
                .in_("dog_id", dog_ids)
                .eq("slot", "main")
                .execute()
            )
            for p in (photos_res.data or []):
                photos_by_dog[p["dog_id"]] = (
                    sb.storage.from_("dog-photos").get_public_url(p["storage_path"])
                )
        except Exception:
            logger.exception("failed to fetch profile photos for candidates")

    return [
        {
            "dog_id": r["dog_id"],
            "name": dogs_by_id.get(r["dog_id"], {}).get("name") or "Unknown",
            "breed": dogs_by_id.get(r["dog_id"], {}).get("breed"),
            "match_score": float(r["similarity"]) if r.get("similarity") is not None else None,
            "photo_url": photos_by_dog.get(r["dog_id"]),
        }
        for r in rows
        if r.get("dog_id")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run_pipeline(embedding_job_id: UUID) -> None:
    channel = inference_channel(embedding_job_id)
    sb = get_supabase()

    try:
        job_lookup = (
            sb.table("embedding_jobs")
            .select("intent, dog_id, embedding_version, user_id")
            .eq("id", str(embedding_job_id))
            .limit(1)
            .execute()
        )
        if not job_lookup.data:
            raise RuntimeError(f"embedding_job {embedding_job_id} not found")
        job = job_lookup.data[0]
        intent = job["intent"]
        dog_id = job.get("dog_id")
        prev_version = job.get("embedding_version") or 1

        # ── Fetch frames ───────────────────────────────────────────────────
        frames = await asyncio.to_thread(_fetch_frames, sb, embedding_job_id)
        if not frames:
            raise RuntimeError(f"no nose_scan_frames found for job {embedding_job_id}")

        # ── Step 1: quality gating ─────────────────────────────────────────
        await _emit_status(embedding_job_id, "quality_check")
        quality_notes: dict = {}
        passing_blobs: list[bytes] = []
        for f in frames:
            label = f"crop_{f['frame_index'] + 1}"
            blob = f["blob"]
            if blob is None:
                ok, notes = False, {"passed": False, "reason": "download_failed"}
            else:
                ok, notes = await asyncio.to_thread(quality_check, blob)
            quality_notes[label] = notes
            status = FrameStatus.SELECTED.value if ok else FrameStatus.REJECTED.value
            await asyncio.to_thread(_mark_frame, sb, f["frame_id"], ok, status, notes)
            if ok:
                passing_blobs.append(blob)

        if len(passing_blobs) < MIN_PASSING_CROPS:
            now_iso = datetime.now(timezone.utc).isoformat()
            sb.table("embedding_jobs").update({
                "status": EmbeddingJobStatus.FAILED.value,
                "quality_passed": False,
                "quality_notes": quality_notes,
                "updated_at": now_iso,
            }).eq("id", str(embedding_job_id)).execute()
            sb.table("registrations").update({
                "status": "failed",
                "updated_at": now_iso,
            }).eq("embedding_job_id", str(embedding_job_id)).execute()
            await publish(channel, {
                "type": "error",
                "data": {
                    "status": "failed",
                    "reason": "quality_check_failed",
                    "passed_count": len(passing_blobs),
                    "required": MIN_PASSING_CROPS,
                    "notes": quality_notes,
                },
            })
            return

        # ── Step 2: embed each passing crop ────────────────────────────────
        await _emit_status(embedding_job_id, "generating_embedding")

        def _build_batch_and_embed(blobs: list[bytes]) -> torch.Tensor:
            tensors = [preprocess_for_inference(b) for b in blobs]
            batch = torch.stack(tensors)
            return embed_batch(batch)

        emb_batch = await asyncio.to_thread(_build_batch_and_embed, passing_blobs)  # (N, D)
        final_emb_t = F.normalize(emb_batch.mean(dim=0), dim=0)
        embedding_list = final_emb_t.tolist()

        # ── Step 3 (enrollment): candidate search + stash, always review ──
        if intent == EmbeddingJobIntent.ENROLLMENT.value:
            await _emit_status(embedding_job_id, "duplicate_check")
            candidates = await asyncio.to_thread(
                _query_candidates,
                sb,
                embedding_list,
                settings.duplicate_suggest_threshold,
                settings.duplicate_candidate_count,
            )

            await _emit_status(embedding_job_id, "saving_results")
            now_iso = datetime.now(timezone.utc).isoformat()

            # Stash pending_embedding; never create a dog here.
            # The human taps POST /inference/{job_id}/resolve to commit.
            sb.table("embedding_jobs").update({
                "status": EmbeddingJobStatus.COMPLETE.value,
                "pending_embedding": embedding_list,
                "duplicate_found": bool(candidates),
                "top_matches": candidates or None,
                "quality_passed": True,
                "quality_notes": quality_notes,
                "embedding_version": 1,
                "completed_at": now_iso,
                "updated_at": now_iso,
            }).eq("id", str(embedding_job_id)).execute()

            sb.table("registrations").update({
                "status": "possible_duplicate",
                "updated_at": now_iso,
            }).eq("embedding_job_id", str(embedding_job_id)).execute()

            await publish(channel, {
                "type": "review_required",
                "data": {
                    "status": "possible_duplicate",
                    "candidates": candidates,
                },
            })
            return

        # ── Step 3 (rescan): flip active embedding, insert new ─────────────
        if not dog_id:
            raise RuntimeError(f"rescan job {embedding_job_id} missing dog_id")

        await _emit_status(embedding_job_id, "saving_results")
        now_iso = datetime.now(timezone.utc).isoformat()

        sb.table("dog_embeddings").update({"is_active": False}) \
            .eq("dog_id", dog_id).eq("is_active", True).execute()

        sb.table("dog_embeddings").insert({
            "dog_id": dog_id,
            "embedding_job_id": str(embedding_job_id),
            "embedding": embedding_list,
            "is_active": True,
            "created_at": now_iso,
        }).execute()

        new_version = prev_version + 1
        sb.table("embedding_jobs").update({
            "status": EmbeddingJobStatus.COMPLETE.value,
            "quality_passed": True,
            "quality_notes": quality_notes,
            "embedding_version": new_version,
            "completed_at": now_iso,
            "updated_at": now_iso,
        }).eq("id", str(embedding_job_id)).execute()

        sb.table("registrations").update({
            "status": "completed",
            "updated_at": now_iso,
        }).eq("embedding_job_id", str(embedding_job_id)).execute()

        await publish(channel, {
            "type": "complete",
            "data": {
                "status": "complete",
                "dog_id": dog_id,
                "embedding_version": new_version,
            },
        })

    except Exception as exc:
        logger.exception("pipeline failed for %s", embedding_job_id)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            sb.table("embedding_jobs").update({
                "status": EmbeddingJobStatus.FAILED.value,
                "updated_at": now_iso,
            }).eq("id", str(embedding_job_id)).execute()
            sb.table("registrations").update({
                "status": "failed",
                "updated_at": now_iso,
            }).eq("embedding_job_id", str(embedding_job_id)).execute()
        except Exception:
            logger.exception("failed to mark job %s failed", embedding_job_id)
        try:
            await publish(channel, {
                "type": "error",
                "data": {"status": "failed", "reason": str(exc)},
            })
        except Exception:
            logger.exception("failed to publish error event for %s", embedding_job_id)
