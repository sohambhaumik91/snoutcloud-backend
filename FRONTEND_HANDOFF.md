# Frontend Handoff — Nose Biometrics Enrollment & Rescan

This document is everything a React Native / web client needs to integrate the
nose-biometrics endpoints, all of which are live today:

1. `POST /registration/start` — begin enrolling a new dog
2. `POST /rescan/start` — re-upload nose crops for an existing dog
3. `POST /inference/start` — trigger the embedding pipeline
4. `GET /inference/status/{embedding_job_id}` — SSE live status stream
5. `GET /inference/result/{embedding_job_id}` — polling fallback for dropped SSE
6. `POST /inference/{embedding_job_id}/resolve` — commit human's enroll/duplicate decision

> Inference runs in an always-on background worker; `/inference/start` returns
> immediately. Subscribe to the SSE stream for live progress and fall back to
> the result endpoint if the connection drops (see §7).

---

## 1. Base URL & Authentication

**Base URL** (configure per environment):
- **Local laptop server via ngrok:** the `https://<random>.ngrok-free.app` URL
  printed at `http://localhost:4040` after `docker compose up`. It changes every
  restart on the free tier — update the client's base URL each time, or use a
  reserved ngrok domain.
- **Railway:** `https://<your-railway-service>.railway.app`.

> **ngrok-free gotcha:** the free tier injects a browser-warning interstitial on
> requests that look like a browser. React Native `fetch`/`EventSource` normally
> bypass it, but if you see an HTML warning page instead of JSON, add the header
> `ngrok-skip-browser-warning: 1` to your `fetch` calls. (The SSE `EventSource`
> can't set headers — if the stream returns HTML, switch to a reserved ngrok
> domain or deploy to Railway.)

Every request must include the Supabase access token in the `Authorization`
header:

```
Authorization: Bearer <supabase_access_token>
```

Get the token from `supabase-js`:

```ts
import { supabase } from './supabase'

const { data: { session } } = await supabase.auth.getSession()
const accessToken = session?.access_token
```

The backend extracts the user id from the JWT `sub` claim. **Never put a
`user_id` in the request body** — the backend ignores anything you send and
trusts only the JWT.

### Token refresh

`supabase-js` refreshes the access token automatically before expiry. If you
get a `401` response from the backend, call `supabase.auth.refreshSession()`
and retry the request once.

---

## 2. End-to-end flow

```
┌────────────────────────────────────────────────────────────────────┐
│  Enrollment                                                        │
│                                                                    │
│  1. POST /registration/start                                       │
│     → { registration_id, embedding_job_id, presigned_urls[8],      │
│         expires_at }                                               │
│                                                                    │
│  2. For each presigned_url, PUT the cropped JPEG bytes directly    │
│     to Supabase Storage (crops do NOT pass through this backend)   │
│                                                                    │
│  3. POST /inference/start { embedding_job_id }                     │
│     → { embedding_job_id, status: "processing" }                   │
│                                                                    │
│  4. Subscribe to GET /inference/status/{embedding_job_id} (SSE)    │
│     for live progress; fall back to GET /inference/result/{id}     │
│     if the SSE connection drops.                                   │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  Rescan (existing dog)                                             │
│                                                                    │
│  1. POST /rescan/start { dog_id }                                  │
│     → { embedding_job_id, presigned_urls[8] }                      │
│                                                                    │
│  2. Upload crops (same as enrollment step 2)                       │
│                                                                    │
│  3. POST /inference/start { embedding_job_id }                     │
│     (same as enrollment step 3)                                    │
└────────────────────────────────────────────────────────────────────┘
```

The TTL: registration's `expires_at` is **30 minutes** from `/registration/start`.
The presigned upload URLs themselves remain valid for **~2 hours** (Supabase
default — not configurable from the SDK). Both clocks start at
`/registration/start` time, so plan the UX around the 30-minute registration
window, not the upload window.

---

## 3. Endpoint reference

### 3.1 `POST /registration/start`

**Auth**: required.
**Request body**: none (send `{}` or no body).
**Response 200**:

```json
{
  "registration_id": "ec1a05b8-2f43-4f60-9d54-7c80a5e0d12c",
  "embedding_job_id": "7e57c4d6-...-...",
  "presigned_urls": [
    {
      "index": 1,
      "url": "https://<project>.supabase.co/storage/v1/object/upload/sign/dog_nose_crops/private/nose-crops/pending/<job_id>/crop_1.jpg?token=...",
      "path": "private/nose-crops/pending/<job_id>/crop_1.jpg",
      "token": "<upload-token>"
    },
    { "index": 2, "url": "...", "path": "...", "token": "..." },
    ...
    { "index": 8, "url": "...", "path": "...", "token": "..." }
  ],
  "expires_at": "2026-05-24T12:34:56+00:00"
}
```

**Errors**:
- `401` — missing/invalid JWT.
- `500` — Supabase insert or presigned URL generation failed. Safe to retry;
  failed inserts are rolled back server-side.

---

### 3.2 `POST /rescan/start`

**Auth**: required.
**Request body**:

```json
{ "dog_id": "uuid-of-an-existing-dog-you-own" }
```

**Response 200**:

```json
{
  "registration_id": "...",
  "embedding_job_id": "...",
  "presigned_urls": [
    { "index": 1, "url": "...", "path": "private/nose-crops/<dog_id>/<job_id>/crop_1.jpg", "token": "..." },
    ...
  ]
}
```

> A rescan now also creates a `registrations` row (one row per scan attempt,
> tracked via `attempt_number`), so the response includes `registration_id` for
> symmetry with enrollment. You generally only need `embedding_job_id` to drive
> upload + inference + status.

**Errors**:
- `401` — missing/invalid JWT.
- `403` — the JWT subject does not own the supplied `dog_id`.
- `404` — `dog_id` does not exist.
- `500` — DB / presigned URL failure (safe to retry).

---

### 3.3 `POST /inference/start`

**Auth**: required.
**Request body**:

```json
{ "embedding_job_id": "uuid-returned-by-registration-or-rescan" }
```

**Response 200** (immediate; pipeline runs async):

```json
{ "embedding_job_id": "...", "status": "processing" }
```

**Errors**:
- `401` — missing/invalid JWT.
- `403` — the job belongs to another user.
- `404` — `embedding_job_id` does not exist.
- `400` — the job is not in `pending` state (already processing / complete /
  failed). The detail string includes the actual status, e.g.
  `"Embedding job is not pending (current status: processing)"`.
- `409` — a concurrent `/inference/start` for the same job won the race.
  Treat the same as `400`: the pipeline is already running, do not retry.

> Important: only call `/inference/start` **after** all 8 crops have finished
> uploading. There is no server-side check that the crops are present yet;
> the pipeline will simply fail or hang.

---

## 3.4 Storage bucket reference

- **Bucket name**: `dog_nose_crops` (private).
- **Path prefix**: every object key starts with `private/...`. This is
  required by the bucket's RLS policy:

  ```
  bucket_id = 'dog_nose_crops'
    AND (storage.foldername(name))[1] = 'private'
    AND auth.role() = 'authenticated'
  ```

  The backend already prepends `private/` to every path it returns, so you do
  not need to add it yourself. **But**: if you ever construct a path on the
  client (for direct `supabase.storage.from('dog_nose_crops').download(path)`
  calls to show crops back to the user), that path must also start with
  `private/`, and the user must be authenticated in `supabase-js` for the
  RLS check to pass.

---

## 4. Uploading crops to Supabase Storage

The backend hands you signed *upload* URLs. Each entry in `presigned_urls`
has three fields:

- `url` — full signed URL (works with plain HTTP `PUT`).
- `path` — the object key inside the `dog_nose_crops` bucket.
- `token` — the upload token, also embedded in `url`. Provided separately so
  you can use the Supabase JS SDK's `uploadToSignedUrl()` helper if you
  prefer it over raw `fetch`.

### Option A — raw `fetch` (works in React Native and the browser)

```ts
async function uploadCrop(presigned: { url: string }, blob: Blob) {
  const res = await fetch(presigned.url, {
    method: 'PUT',
    headers: { 'Content-Type': 'image/jpeg' },
    body: blob,
  })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
}
```

### Option B — Supabase JS SDK

```ts
await supabase
  .storage
  .from('dog_nose_crops')
  .uploadToSignedUrl(presigned.path, presigned.token, blob, {
    contentType: 'image/jpeg',
  })
```

### Uploading all 8 in parallel

```ts
async function uploadAllCrops(
  presigned_urls: Array<{ index: number; url: string }>,
  crops: Blob[]  // length 8, ordered to match index 1..8
) {
  await Promise.all(
    presigned_urls.map((p) => uploadCrop(p, crops[p.index - 1]))
  )
}
```

Show a per-crop progress UI by resolving `Promise.all` against an array of
individually-tracked promises. If one upload fails, you can retry that crop
with the same presigned URL — it stays valid for the full 2-hour window.

---

## 5. Reference TypeScript types

```ts
export interface PresignedUpload {
  index: number      // 1..8
  url: string
  path: string
  token: string
}

export interface RegistrationStartResponse {
  registration_id: string  // uuid
  embedding_job_id: string // uuid
  presigned_urls: PresignedUpload[]   // length 8
  expires_at: string                  // ISO8601
}

export interface RescanStartResponse {
  registration_id: string  // uuid
  embedding_job_id: string
  presigned_urls: PresignedUpload[]   // length 8
}

export interface InferenceStartResponse {
  embedding_job_id: string
  status: 'processing'
}

// SSE events on GET /inference/status/{embedding_job_id}?token=<jwt>
// event: status   → { status: 'processing', step: 'quality_check' | 'generating_embedding' | 'duplicate_check' | 'saving_results' | 'starting' }
// event: complete → { status: 'complete', duplicate_found, duplicate_dog_id, match_score, dog_id, embedding_version }
// event: error    → { status: 'failed', reason, notes? }
```

---

## 6. Minimal end-to-end client

```ts
import { supabase } from './supabase'

const API_BASE = process.env.EXPO_PUBLIC_API_BASE_URL!

async function authedFetch(path: string, init: RequestInit = {}) {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('Not signed in')

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json()
}

export async function enrollDog(crops: Blob[]) {
  if (crops.length !== 8) throw new Error('Need exactly 8 crops')

  // 1. start registration
  const reg: RegistrationStartResponse = await authedFetch(
    '/registration/start',
    { method: 'POST', body: '{}' }
  )

  // 2. upload crops in parallel
  await Promise.all(
    reg.presigned_urls.map((p) =>
      fetch(p.url, {
        method: 'PUT',
        headers: { 'Content-Type': 'image/jpeg' },
        body: crops[p.index - 1],
      }).then((r) => {
        if (!r.ok) throw new Error(`Crop ${p.index} upload failed: ${r.status}`)
      })
    )
  )

  // 3. kick off the pipeline
  await authedFetch('/inference/start', {
    method: 'POST',
    body: JSON.stringify({ embedding_job_id: reg.embedding_job_id }),
  })

  return reg  // contains registration_id + embedding_job_id for tracking
}

export async function rescanDog(dogId: string, crops: Blob[]) {
  if (crops.length !== 8) throw new Error('Need exactly 8 crops')

  const r: RescanStartResponse = await authedFetch('/rescan/start', {
    method: 'POST',
    body: JSON.stringify({ dog_id: dogId }),
  })

  await Promise.all(
    r.presigned_urls.map((p) =>
      fetch(p.url, {
        method: 'PUT',
        headers: { 'Content-Type': 'image/jpeg' },
        body: crops[p.index - 1],
      }).then((r2) => {
        if (!r2.ok) throw new Error(`Crop ${p.index} upload failed: ${r2.status}`)
      })
    )
  )

  await authedFetch('/inference/start', {
    method: 'POST',
    body: JSON.stringify({ embedding_job_id: r.embedding_job_id }),
  })

  return r
}
```

---

## 7. Live status (SSE) + polling fallback

### SSE — `GET /inference/status/{embedding_job_id}?token=<jwt>`

`EventSource` in React Native can't set headers, so pass the Supabase access
token as the `?token=` query param. The stream emits the current DB state on
connect, then `status` step events, and closes on `complete` / `error`.

```ts
const { data: { session } } = await supabase.auth.getSession()
const es = new EventSource(
  `${API_BASE}/inference/status/${embeddingJobId}?token=${session!.access_token}`
)

es.addEventListener('status', (e) => {
  const { step } = JSON.parse(e.data)   // quality_check | generating_embedding | duplicate_check | saving_results
  updateProgress(step)
})

es.addEventListener('complete', (e) => {
  const r = JSON.parse(e.data)
  es.close()
  if (r.duplicate_found) showDuplicateScreen(r.duplicate_dog_id, r.match_score)
  else showSuccess(r.dog_id, r.embedding_version)
})

es.addEventListener('error', (e) => {
  es.close()
  // If the connection (not the pipeline) dropped, fall back to the result endpoint.
})
```

### Polling fallback — `GET /inference/result/{embedding_job_id}`

Auth via the normal `Authorization: Bearer` header. Returns the job's terminal
fields. Use it if the SSE connection drops before `complete`:

```json
{
  "embedding_job_id": "...",
  "status": "complete | processing | failed",
  "intent": "enrollment | rescan",
  "dog_id": "uuid | null",
  "duplicate_found": false,
  "duplicate_dog_id": null,
  "match_score": null,
  "quality_passed": true,
  "quality_notes": { "crop_1": { "passed": true, ... }, ... },
  "embedding_version": 1,
  "completed_at": "ISO8601 | null"
}
```

Terminal `status`:
- `complete` — check `duplicate_found`. If true and `intent === 'enrollment'`,
  route to the duplicate-resolution screen with `duplicate_dog_id` + `match_score`.
- `failed` — `quality_notes` carries per-crop reasons. Show a retry prompt.

If still `processing`, poll every 2–3s (cap ~60s) or just reconnect the SSE stream.

---

## 8. Error-handling checklist

| Case | What to do |
|---|---|
| `401` from any endpoint | Refresh session via `supabase.auth.refreshSession()`, retry once. If it still fails, kick the user back to login. |
| `403` on `/rescan/start` | The user does not own that dog. Should not happen if you only show their dogs in the UI — treat as a bug and log it. |
| `403` on `/inference/start` | Same: bug. The job was created under a different user; do not retry. |
| `404` on `/rescan/start` or `/inference/start` | Stale id (e.g. cached from a prior session). Refresh the dog/job list and re-prompt. |
| `400` on `/inference/start` | The job is already past `pending`. Skip straight to the polling/SSE wait — do not re-call `/inference/start`. |
| `409` on `/inference/start` | A concurrent call won the race. Same handling as `400`. |
| `500` | Show a generic retry prompt. `/registration/start` and `/rescan/start` are safe to retry (rolled back server-side on partial failure). |
| Upload `PUT` fails | Retry the specific failing crop with the same presigned URL. |
| Registration window expired (30 min after `/registration/start`) | Restart from `/registration/start`; presigned URLs are scoped to a specific job. |

---

## 9. Key invariants the frontend must respect

- Exactly **8 crops**, indexed 1..8. Match each crop to its `presigned_urls[i].index`.
- Each crop is a JPEG. Set `Content-Type: image/jpeg` on the upload `PUT`.
- Do not include any `user_id` in request bodies. The backend derives it from
  the JWT `sub` and would ignore yours.
- Only call `/inference/start` once per `embedding_job_id`. The backend
  enforces this (400/409), but avoid the wasted round trip.
- A single `embedding_job_id` ↔ a single set of 8 crops ↔ a single pipeline
  run. To re-try a failed run you must create a brand-new job via
  `/registration/start` or `/rescan/start`.

---

## 10. Notes

- Inference runs in an always-on background worker, so terminal status can lag
  `/inference/start` by a few seconds. Drive the UI off the SSE stream (§7).
- `duplicate_found` is only ever true for `intent === 'enrollment'`; rescans skip
  the duplicate check.
