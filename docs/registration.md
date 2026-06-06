# Nose Biometrics — Registration & Inference Pipeline

> Canonical strategy doc for the nose-print enrollment / rescan pipeline.
> This describes what the code actually does. `CLAUDE.md` points here for the
> pipeline detail; `FRONTEND_HANDOFF.md` is the client-facing contract.

## Overview

Two scopes:

1. **Start** (`/registration/start`, `/rescan/start`) — create the job + scan
   attempt + per-frame rows, hand back presigned upload URLs. Blocking and fast;
   no ML work, no downloads.
2. **Inference** (`/inference/start` → arq worker) — quality-gate the uploaded
   crops, embed them, dedup-check (enrollment only), persist results.

FastAPI handles the HTTP layer. An **always-on arq worker** (separate process,
same Redis) runs inference in the background — a job survives a web/worker
restart, unlike the old FastAPI `BackgroundTasks`. Redis pub/sub carries status
to the client over SSE.

## Tables

### `embedding_jobs`
Infrastructure record — one row per pipeline run (enrollment or rescan).
`intent` (`enrollment` | `rescan`) distinguishes them. `status` runs
`pending → processing → complete | failed`. Carries the duplicate/quality
results and `embedding_version`. `registration_id` links back to the scan
attempt (set in both flows). It has **no `storage_path` column** — crops are
resolved via `nose_scan_frames` on `job_id`.

### `registrations` — one row per **scan attempt**
Not enrollment-only. Every scan — enrollment *and* rescan — gets a row, so
`nose_scan_frames` always has a `registration_id` to hang off.

- **Enrollment** → `attempt_number = 1`; `dog_id` is null until the pipeline
  completes, then backfilled.
- **Rescan** → `dog_id` known up front; `attempt_number = MAX(existing for dog) + 1`.
- `SELECT * FROM registrations WHERE dog_id = X ORDER BY attempt_number` is the
  full enrollment→rescan timeline for a dog.

### `nose_scan_frames` — one row per crop (8 per scan)
`registration_id` (NOT NULL, FK, `ON DELETE CASCADE`) + universal `job_id`.
`frame_index` is 0-based (DB CHECK 0..11) = client `index` (1..8) minus one.
`status` (`frame_status`: `uploaded` → `selected` | `rejected`),
`selected_for_embed`, `ml_metadata` (per-frame quality result written by the
worker). Crops are resolved by querying this table (by `job_id`), **never** by
listing storage.

### `dog_embeddings`
Versioned `VECTOR(384)` per dog (matches the trained NoseEncoder — *not* 512).
`is_active = true` marks the row used for matching. Enrollment and rescan each
append a row; rescan flips the prior row inactive first.

## Status lifecycle (`registrations.status`)

```
initiated → processing → completed              (enrollment, no dup)
                       → possible_duplicate      (enrollment, dup found)
                       → failed                  (quality gate / pipeline error)
```
Rescan uses `initiated → processing → completed | failed` (no duplicate check).

## Endpoints

All require `Authorization: Bearer <jwt>` except `/health`. The user id comes
from the JWT `sub` only — never the body.

### `POST /registration/start`
Body: none. Creates `embedding_jobs` (`intent=enrollment`, `pending`), a
`registrations` row (`initiated`, `attempt_number=1`), and **8**
`nose_scan_frames` (`uploaded`). Returns `registration_id`, `embedding_job_id`,
8 presigned URLs, `expires_at` (+30 min).

### `POST /rescan/start`
Body: `{ dog_id }`. Verifies ownership. Creates `embedding_jobs`
(`intent=rescan`), a `registrations` row (`attempt_number = N+1`, `dog_id` set),
links them both ways, and 8 `nose_scan_frames`. Returns `registration_id`,
`embedding_job_id`, 8 presigned URLs.

### `POST /inference/start`
Body: `{ embedding_job_id }`. CAS-flips the job `pending → processing` (409 on
race), sets the enrollment registration to `processing`, then **enqueues an arq
job** (reverts to `pending` if the enqueue fails). Returns immediately.

### `GET /inference/status/{embedding_job_id}` — SSE
JWT via `?token=` (RN `EventSource` can't set headers). Emits current DB state
on connect, then forwards worker step events. Heartbeats every 15s; closes on
`complete`/`error`. Steps: `quality_check → generating_embedding →
duplicate_check → saving_results`.

### `GET /inference/result/{embedding_job_id}`
Polling fallback for dropped SSE — returns the job's terminal fields.

## arq worker

Always-on, run as `arq app.worker.WorkerSettings` (a separate process / Railway
service / docker-compose `worker`). The ~380MB checkpoint is **baked into the
Docker image** at build (`models/best_supcon_clahe_gem.pt` → loaded from
`NOSE_MODEL_PATH=/app/models/best_supcon_clahe_gem.pt`); the web process never
loads it. (Baked rather than downloaded because the file is too large for git and
exceeds Supabase Storage's 50MB upload cap.) Payload is the bare
`embedding_job_id`; the worker resolves everything else from the DB.

`run_pipeline(embedding_job_id)`:

1. **Fetch frames** — query `nose_scan_frames` by `job_id`, download each crop.
2. **Quality gate** (emit `quality_check`) — per crop: min-dim, Laplacian
   sharpness, brightness (+ `download_failed`). Write each frame's
   `selected_for_embed` / `status` / `ml_metadata`; aggregate into
   `embedding_jobs.quality_notes`. If `< 6` pass → job `failed`, registration
   `failed`, emit `error`.
3. **Embed** (emit `generating_embedding`) — CLAHE → ConvNeXt-Tiny per passing
   crop, L2-normalise, mean, re-normalise → `VECTOR(384)`.
4. **Duplicate check, enrollment only** (emit `duplicate_check`) — `match_nose_embedding`
   RPC (cosine). `> 0.85` → flag duplicate.
5. **Persist** (emit `saving_results`):
   - Enrollment, dup → job `complete` + `duplicate_*`, registration
     `possible_duplicate`. No dog/embedding created.
   - Enrollment, no dup → create `dogs`, insert active `dog_embeddings`, job
     `complete` (+ `dog_id`), registration `completed` (+ `dog_id`).
   - Rescan → flip prior `dog_embeddings.is_active=false`, insert new active
     row, job `complete` with `embedding_version = prev + 1`.
6. Emit `complete`. On any exception: job `failed`, emit `error`.

> **Known gap:** a hard worker crash mid-run leaves the job stuck in
> `processing` (arq `max_tries=1`, no idempotent re-run). A stale-`processing`
> reaper is future work.

## Redis pub/sub

Channel `inference:{embedding_job_id}`. The worker publishes
`{type, data}` events; the SSE handler subscribes and forwards. DB is the source
of truth — status is always written to the DB before publishing.

## Storage

Bucket `dog_nose_crops` (private). Every key starts with `private/` (RLS).
Presigned upload URLs are generated server-side with the service-role key; crops
are PUT directly to Supabase and never pass through the backend.

```
Enrollment:  private/nose-crops/pending/{job_id}/crop_{1..8}.jpg
Rescan:      private/nose-crops/{dog_id}/{job_id}/crop_{1..8}.jpg
```

## Key constraints

- **Worker is always-on**, never per-request. `arq app.worker.WorkerSettings`.
- **Frames are the source of truth** for which crops exist — query by `job_id`.
- **Job payload is minimal** — just `embedding_job_id`.
- **DB before pub/sub** — write status, then publish.
- **8 crops, indexed 1..8** (client) ↔ `frame_index 0..7` (DB).
- **`registrations` = one row per scan attempt**, tracked by `attempt_number`.
