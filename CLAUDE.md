# SnoutCloud — Nose Crop Embedding Pipeline

## Overview

This document describes the end-to-end architecture for SnoutCloud's dog nose-print registration
and re-scan pipeline. It covers authentication, database schema, storage conventions, FastAPI
endpoints, SSE status streaming, and ML inference flow.

This is the canonical reference for Claude Code to implement the Railway FastAPI backend.

> **The registration/inference pipeline detail lives in [`docs/registration.md`](docs/registration.md)** —
> that is the single source of truth for the flow (endpoints, the arq worker, `nose_scan_frames`,
> `attempt_number`). This file keeps the auth + schema + storage reference and links out for the
> pipeline so the two don't drift.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Mobile App | React Native + Expo (Dev Client) |
| Auth | Supabase Auth (OAuth via Google) |
| Database | Supabase Postgres (pgvector 0.8.0 enabled) |
| Object Storage | Supabase Storage (`dog_nose_crops` bucket, private) |
| Backend | FastAPI on Railway |
| Job Queue | arq (Redis-backed) — always-on worker process runs inference |
| ML Inference | ConvNeXt-Tiny encoder (SupCon, GeM pooling) → `VECTOR(384)` |
| Embedding Store | pgvector on Supabase (`dog_embeddings` table) |
| SSE | FastAPI `StreamingResponse` → React Native `EventSource` |

---

## Authentication Architecture

### Who issues the JWT

Supabase Auth issues all JWTs (RS256). The mobile app never manages token signing or storage
explicitly — `supabase-js` handles session storage in AsyncStorage and auto-refreshes tokens
before expiry.

### How the app sends the JWT to Railway

Every request from the React Native app to the FastAPI backend includes the Supabase JWT in
the `Authorization` header:

```
Authorization: Bearer <supabase_access_token>
```

Retrieve the token in the app:

```typescript
const { data: { session } } = await supabase.auth.getSession()
const token = session.access_token
```

### How Railway verifies the JWT (JWKS)

Railway never trusts the client blindly. Every endpoint (except `/health`) runs JWT verification
as a FastAPI dependency before executing any logic.

Supabase publishes a JWKS endpoint:

```
https://<your-supabase-project>.supabase.co/auth/v1/.well-known/jwks.json
```

Railway fetches the public signing keys from this endpoint and verifies the JWT signature,
expiry, and audience locally — no round trip to Supabase on every request.

```python
# auth/jwt.py
import os
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SUPABASE_URL = os.environ["SUPABASE_URL"]
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"

jwks_client = PyJWKClient(JWKS_URL)  # caches keys, refreshes automatically
bearer = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer)
) -> dict:
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience="authenticated"
        )
        return payload  # contains: sub (user_id), email, role, exp
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
```

`user["sub"]` is the authenticated Supabase user UUID. Never trust a `user_id` passed in the
request body — always extract it from the verified JWT payload.

### Environment Variables (Railway)

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role_key>   # never expose to client
SUPABASE_ANON_KEY=<anon_key>
WEBHOOK_SECRET=<shared_secret_for_storage_webhooks>
REDIS_URL=<redis_connection_string>            # if SSE uses Redis pub/sub
```

The `service_role_key` bypasses RLS entirely. Railway uses it to write inference results back
to Supabase. It must never appear in the mobile app.

---

## Database Schema

### `embedding_jobs`

Tracks every ML pipeline execution — enrollment or rescan. This is the infrastructure-level
job record, separate from the application-level `registrations` record.

```sql
CREATE TYPE embedding_job_intent AS ENUM ('enrollment', 'rescan');

CREATE TYPE embedding_job_status AS ENUM (
  'pending',
  'uploading',
  'processing',
  'complete',
  'failed'
);

CREATE TABLE public.embedding_jobs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id     UUID REFERENCES public.registrations(registration_id), -- null for rescan
  dog_id              UUID REFERENCES public.dogs(id),                        -- null during enrollment until dog row created
  user_id             UUID NOT NULL REFERENCES public.users(id),
  intent              embedding_job_intent NOT NULL,
  status              embedding_job_status NOT NULL DEFAULT 'pending',
  -- NOTE: the live table has NO storage_path column. The full per-crop path is
  -- stored on each nose_scan_frames row; the job is resolved to its crops by
  -- querying nose_scan_frames on job_id.
  duplicate_found     BOOLEAN,
  duplicate_dog_id    UUID REFERENCES public.dogs(id),
  match_score         FLOAT,
  quality_passed      BOOLEAN,
  quality_notes       JSONB,                                                  -- per-crop quality results
  embedding_version   INT NOT NULL DEFAULT 1,                                 -- increments on rescan
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at        TIMESTAMPTZ
);
```

### `registrations` (altered)

Tracks the user's intent to enroll a new dog. Purely application-level — expiry, attempt
tracking, enrollment lifecycle.

```sql
-- Run after embedding_jobs table is created
ALTER TABLE public.registrations
  DROP COLUMN job_id,
  DROP COLUMN expected_frames,
  DROP COLUMN matched_dog_id,
  DROP COLUMN match_score;

ALTER TABLE public.registrations
  ADD COLUMN embedding_job_id UUID REFERENCES public.embedding_jobs(id);
```

Final `registrations` columns:

```
registration_id     UUID PK
user_id             UUID FK → users.id
dog_id              UUID FK → dogs.id         (null until enrollment completes)
embedding_job_id    UUID FK → embedding_jobs.id
attempt_number      INTEGER
status              registration_status        ('initiated','uploading','processing',
                                                'completed','failed','abandoned','possible_duplicate')
expires_at          TIMESTAMPTZ
created_at          TIMESTAMPTZ
updated_at          TIMESTAMPTZ
```

### `dog_embeddings`

Stores versioned nose embeddings (`VECTOR(384)`, ConvNeXt-Tiny / SupCon) per dog. Every enrollment and rescan appends a row.
`is_active = true` marks the embedding currently used for nearest-neighbour matching.

```sql
CREATE TABLE public.dog_embeddings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dog_id              UUID NOT NULL REFERENCES public.dogs(id),
  embedding_job_id    UUID NOT NULL REFERENCES public.embedding_jobs(id),
  embedding           VECTOR(384) NOT NULL,
  is_active           BOOLEAN NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast nearest-neighbour search (cosine distance)
CREATE INDEX ON public.dog_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Partial index — only active embeddings are ever queried for matching
CREATE INDEX ON public.dog_embeddings (dog_id) WHERE is_active = true;
```

When a rescan completes successfully:
1. Set previous `dog_embeddings` row for this `dog_id` to `is_active = false`
2. Insert new row with `is_active = true`
3. Increment `embedding_version` in `embedding_jobs`

---

## Storage Convention

**Bucket:** `dog_nose_crops` (private, no public access). Every object key is prefixed with
`private/` to satisfy the bucket RLS policy `(storage.foldername(name))[1] = 'private'`.

**Path structure:**

```
# During enrollment (dog_id not yet assigned)
nose-crops/pending/{embedding_job_id}/crop_1.jpg
nose-crops/pending/{embedding_job_id}/crop_2.jpg
...
nose-crops/pending/{embedding_job_id}/crop_8.jpg

# After enrollment completes (optional move, or leave as-is and store path in embedding_jobs)
nose-crops/{dog_id}/{embedding_job_id}/crop_1.jpg

# Rescan (dog_id already known)
nose-crops/{dog_id}/{embedding_job_id}/crop_1.jpg
```

`embedding_jobs.storage_path` stores the prefix (e.g. `nose-crops/pending/{job_id}`) so
Railway always knows where to fetch crops from without reconstructing the path.

**Presigned URLs** are generated by Railway using the `service_role_key` and returned to the
client. The client uploads directly to Supabase Storage — crops never pass through Railway.

---

## API Endpoints

All endpoints require `Authorization: Bearer <jwt>` header unless marked public.
Base URL: `https://<your-railway-service>.railway.app`

---

### `GET /health`
**Auth:** None  
**Purpose:** Railway health check.  
**Response:**
```json
{ "status": "ok" }
```

---

### `POST /registration/start`
**Auth:** Required  
**Purpose:** Initiate a new dog enrollment. Creates a `registrations` row, an `embedding_jobs`
row with `intent = 'enrollment'`, and returns 8 presigned upload URLs.

**Request body:**
```json
{}
```

**Response:**
```json
{
  "registration_id": "uuid",
  "embedding_job_id": "uuid",
  "presigned_urls": [
    { "index": 1, "url": "https://...", "path": "nose-crops/pending/{job_id}/crop_1.jpg" },
    { "index": 2, "url": "https://...", "path": "nose-crops/pending/{job_id}/crop_2.jpg" },
    ...
    { "index": 8, "url": "https://...", "path": "nose-crops/pending/{job_id}/crop_8.jpg" }
  ],
  "expires_at": "ISO8601 timestamp"
}
```

**Side effects:**
- Creates `embedding_jobs` row: `intent=enrollment`, `status=pending`,
  `storage_path=nose-crops/pending/{job_id}`
- Creates `registrations` row: `status=initiated`, `embedding_job_id=<job_id>`,
  `expires_at=NOW()+30min`

---

### `POST /rescan/start`
**Auth:** Required  
**Purpose:** Initiate a re-upload of nose crops for an existing dog. No `registration_id`
is created. Returns 8 presigned upload URLs.

**Request body:**
```json
{
  "dog_id": "uuid"
}
```

**Response:**
```json
{
  "embedding_job_id": "uuid",
  "presigned_urls": [
    { "index": 1, "url": "https://...", "path": "nose-crops/{dog_id}/{job_id}/crop_1.jpg" },
    ...
  ]
}
```

**Side effects:**
- Creates `embedding_jobs` row: `intent=rescan`, `status=pending`, `dog_id=<dog_id>`,
  `storage_path=nose-crops/{dog_id}/{job_id}`
- Verifies the requesting user owns the dog (via `dogs.user_id` check against JWT `sub`)

---

### `POST /inference/start`
**Auth:** Required  
**Purpose:** Client calls this after all 8 crops have been uploaded. Triggers the full
pipeline: quality gating → embedding generation → duplicate check (enrollment only) →
result persistence.

**Request body:**
```json
{
  "embedding_job_id": "uuid"
}
```

**Response:** (immediate, pipeline runs async)
```json
{
  "embedding_job_id": "uuid",
  "status": "processing"
}
```

**Side effects:**
- Updates `embedding_jobs.status` → `processing`
- Updates `registrations.status` → `processing` (enrollment only)
- Spawns background task: `run_pipeline(embedding_job_id)`
- Client subscribes to `/inference/status/{embedding_job_id}` SSE stream for updates

---

### `GET /inference/status/{embedding_job_id}` — SSE
**Auth:** Required (JWT passed as query param `?token=<jwt>` since SSE `EventSource` does
not support custom headers in React Native)  
**Purpose:** Streams real-time pipeline status updates to the client.

**SSE event stream:**
```
event: status
data: {"status": "processing", "step": "quality_check"}

event: status
data: {"status": "processing", "step": "generating_embedding"}

event: status
data: {"status": "processing", "step": "duplicate_check"}

event: complete
data: {
  "status": "complete",
  "duplicate_found": false,
  "dog_id": "uuid",
  "embedding_version": 1
}

event: error
data: {"status": "failed", "reason": "quality_check_failed", "notes": [...]}
```

**Steps emitted during pipeline:**
1. `quality_check` — server-side crop quality validation
2. `generating_embedding` — ConvNeXt + ArcFace inference
3. `duplicate_check` — pgvector nearest-neighbour search (enrollment only)
4. `saving_results` — writing to DB

**Implementation note:** FastAPI `StreamingResponse` with `media_type="text/event-stream"`.
Pipeline publishes step updates to an in-memory async queue (or Redis pub/sub if Railway runs
multiple instances). SSE handler reads from the queue and yields events.

```python
# Minimal SSE pattern
from fastapi.responses import StreamingResponse
import asyncio

@router.get("/inference/status/{embedding_job_id}")
async def status_stream(embedding_job_id: str, token: str):
    user = verify_token(token)  # manual verify since no header available
    
    async def event_generator():
        queue = get_or_create_queue(embedding_job_id)
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=60)
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
            if event['type'] in ('complete', 'error'):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

### `GET /inference/result/{embedding_job_id}`
**Auth:** Required  
**Purpose:** Fallback polling endpoint. Returns the final result for a job. Used if the SSE
connection drops before the `complete` event is received.

**Response:**
```json
{
  "embedding_job_id": "uuid",
  "status": "complete",
  "intent": "enrollment",
  "duplicate_found": false,
  "dog_id": "uuid",
  "embedding_version": 1,
  "quality_passed": true,
  "quality_notes": null,
  "completed_at": "ISO8601"
}
```

---

## Pipeline Internals (`run_pipeline`)

Runs in the **always-on arq worker** (`arq app.worker.WorkerSettings`), enqueued
by `/inference/start`. Not a FastAPI `BackgroundTask` — a job survives a
web/worker restart.

The full step-by-step (fetch frames from `nose_scan_frames` → quality gate →
embed `VECTOR(384)` → duplicate check → persist) is documented once, canonically,
in **[`docs/registration.md`](docs/registration.md)**. It is not duplicated here
to avoid drift.

---

## React Native Client Flow

### Enrollment

```typescript
// 1. Start registration
const { registration_id, embedding_job_id, presigned_urls } =
  await api.post('/registration/start')

// 2. Upload 8 crops directly to Supabase Storage using presigned URLs
await Promise.all(
  presigned_urls.map(({ url, index }) =>
    fetch(url, { method: 'PUT', body: cropFrames[index], headers: { 'Content-Type': 'image/jpeg' } })
  )
)

// 3. Trigger inference
await api.post('/inference/start', { embedding_job_id })

// 4. Open SSE stream
const session = await supabase.auth.getSession()
const es = new EventSource(
  `${RAILWAY_URL}/inference/status/${embedding_job_id}?token=${session.data.session.access_token}`
)

es.addEventListener('status', (e) => {
  const { step } = JSON.parse(e.data)
  updateUI(step) // show progress to user
})

es.addEventListener('complete', (e) => {
  const result = JSON.parse(e.data)
  es.close()
  if (result.duplicate_found) {
    navigateToDuplicateScreen(result)
  } else {
    navigateToSuccessScreen(result.dog_id)
  }
})

es.addEventListener('error', (e) => {
  es.close()
  showErrorScreen(JSON.parse(e.data))
})
```

### Rescan

```typescript
// Same flow but different start endpoint
const { embedding_job_id, presigned_urls } =
  await api.post('/rescan/start', { dog_id })

// Upload + SSE stream identical to enrollment
```

---

## Supabase Auth Trigger (reference)

On every new OAuth sign-in, a Postgres trigger automatically creates a row in `public.users`:

```sql
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.users (id, email, name, is_owner, roles, data_sharing_preference, created_at, updated_at)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', split_part(NEW.email, '@', 1)),
    false,
    '{}',
    'minimal',
    NOW(),
    NOW()
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

---

## Key Invariants

- `user_id` is **always** extracted from the verified JWT `sub` claim. Never trust a
  `user_id` in the request body.
- Presigned URLs are generated server-side using `service_role_key`. The client uploads
  directly to Supabase Storage — crops never pass through Railway.
- `embedding_jobs.storage_path` is the prefix; `nose_scan_frames` (keyed by `job_id`) is the
  source of truth for which crops belong to a job.
- Only `is_active = true` embeddings are queried during duplicate check and matching.
- The SSE stream is the primary status delivery mechanism. `/inference/result/{id}` is the
  fallback for dropped connections.
- `registrations` is **one row per scan attempt** — both enrollment AND rescan create one
  (tracked via `attempt_number`). `embedding_jobs.intent` distinguishes the two.
- Railway always writes to Supabase using `service_role_key` (bypasses RLS). The mobile
  app uses `anon_key` + RLS for direct Supabase queries.