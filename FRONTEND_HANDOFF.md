# Frontend Handoff — Nose Biometrics Enrollment & Rescan

This document is everything the React Native iOS client needs to integrate the
nose-biometrics endpoints. All endpoints are live on Railway today.

**Endpoints:**
1. `POST /registration/start` — begin enrolling a new dog
2. `POST /rescan/start` — re-upload nose crops for an existing dog
3. `POST /inference/start` — trigger the embedding pipeline
4. `GET /inference/status/{embedding_job_id}` — SSE live status stream
5. `GET /inference/result/{embedding_job_id}` — polling fallback for dropped SSE
6. `POST /inference/{embedding_job_id}/resolve` — commit human's enroll/duplicate decision

> Inference runs in an always-on background worker; `/inference/start` returns
> immediately. Subscribe to the SSE stream for live progress and fall back to
> the result endpoint if the connection drops (see §8).

---

## 1. Base URL & Authentication

**Base URL** (configure per environment):
- **Production (Railway):** `https://snoutcloud-backend-production.up.railway.app`
- **Local via ngrok:** the `https://<random>.ngrok-free.app` URL printed at
  `http://localhost:4040` after `docker compose up`. Changes every restart on
  the free tier.

Every request must include the Supabase access token in the `Authorization` header:

```
Authorization: Bearer <supabase_access_token>
```

```ts
import { supabase } from './supabase'

const { data: { session } } = await supabase.auth.getSession()
const accessToken = session?.access_token
```

The backend extracts user id from the JWT `sub` claim. **Never put `user_id`
in request bodies** — it is ignored. Only the JWT is trusted.

### Token refresh

If you get a `401`, call `supabase.auth.refreshSession()` and retry once.
`supabase-js` auto-refreshes before expiry but a `401` can still happen at
session boundaries.

---

## 2. iOS camera capture — image format requirements

Crops are captured by the iOS camera (auto-capture, not photo library). The
backend accepts only **JPEG** (`image/jpeg`). Every upload PUT must set
`Content-Type: image/jpeg`.

### HEIC / HEIF — must convert before upload

iPhones shooting in High Efficiency mode produce HEIC files. The backend
rejects HEIC. Convert before uploading:

```ts
import * as ImageManipulator from 'expo-image-manipulator'

async function ensureJpeg(uri: string): Promise<string> {
  const result = await ImageManipulator.manipulateAsync(
    uri,
    [],  // no transforms
    { compress: 0.92, format: ImageManipulator.SaveFormat.JPEG }
  )
  return result.uri  // always a JPEG URI
}
```

Call `ensureJpeg()` on every camera URI before building the upload body.
`expo-image-manipulator` is already JPEG-safe on both iOS and Android.

### Recommended camera library

Use `react-native-vision-camera` or `expo-camera`. Both return a local file
URI. Always pass `quality: 0.92` (or equivalent) and explicitly request JPEG
output:

```ts
// react-native-vision-camera example
const photo = await camera.current.takePhoto({
  flash: 'off',
  enableShutterSound: false,
})
// photo.path is a file URI — may be HEIC on some devices
const jpegUri = await ensureJpeg(`file://${photo.path}`)
```

### Converting URI → fetch-compatible body

```ts
async function uriToBlob(uri: string): Promise<Blob> {
  const res = await fetch(uri)
  return res.blob()
}
```

Or use `ReactNativeBlobUtil` / `expo-file-system` for large files if memory is a concern.

### Minimum image requirements

| Property | Minimum |
|---|---|
| Width | 224 px |
| Height | 224 px |
| Format | JPEG only |
| Content-Type header | `image/jpeg` |

Images below minimum dimensions are rejected by the quality gate and return an
`error` SSE event with `reason: "below_min_dim"`. Capture tightly around the
nose — full-face or body shots will be rejected.

---

## 3. End-to-end flow

### Enrollment — always ends with a human review step

Every enrollment goes through a **review screen** regardless of whether any
matching dogs are found. The worker never auto-creates a dog. The human always taps to confirm.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Enrollment                                                         │
│                                                                     │
│  1. POST /registration/start                                        │
│     → { registration_id, embedding_job_id, presigned_urls[8],       │
│         expires_at }                                                │
│                                                                     │
│  2. Camera auto-captures 8 nose crops → convert to JPEG             │
│     PUT each presigned_url with JPEG bytes directly to              │
│     Supabase Storage (crops do NOT pass through this backend)       │
│                                                                     │
│  3. POST /inference/start { embedding_job_id }                      │
│     → { embedding_job_id, status: "processing" }                    │
│                                                                     │
│  4. Subscribe to GET /inference/status/{id}?token=<jwt> (SSE)       │
│     Worker: quality_check → generating_embedding →                  │
│             duplicate_check → saving_results                        │
│                                                                     │
│  5. SSE emits `review_required` (terminal event):                   │
│     → { status: "possible_duplicate",                               │
│         candidates: [{dog_id, name, breed, match_score}, ...] }     │
│     candidates may be empty — still requires a human tap.           │
│                                                                     │
│  6. Show review screen:                                             │
│     • Candidates present → "Is this one of these dogs?" + buttons  │
│     • No candidates      → "No records found — complete enrollment" │
│                                                                     │
│  7. POST /inference/{embedding_job_id}/resolve                      │
│     { decision: "new" }          → dog created, registration done   │
│     { decision: "existing",                                         │
│       dog_id: "<candidate>" }    → duplicate confirmed, done        │
└─────────────────────────────────────────────────────────────────────┘
```

### Rescan — no review step

```
┌─────────────────────────────────────────────────────────────────────┐
│  Rescan (existing dog)                                              │
│                                                                     │
│  1. POST /rescan/start { dog_id }                                   │
│     → { registration_id, embedding_job_id, presigned_urls[8] }      │
│                                                                     │
│  2. Camera captures 8 crops → convert to JPEG → upload              │
│                                                                     │
│  3. POST /inference/start { embedding_job_id }                      │
│                                                                     │
│  4. SSE emits `complete`:                                           │
│     → { status: "complete", dog_id, embedding_version }             │
│     No review screen. No duplicate check. Done.                     │
└─────────────────────────────────────────────────────────────────────┘
```

The TTL: `expires_at` is **30 minutes** from `/registration/start`. Presigned
upload URLs are valid for **~2 hours**. Plan UX around the 30-minute window.

---

## 4. Endpoint reference

### 4.1 `POST /registration/start`

**Auth**: required. **Body**: none (`{}` or empty).

**Response 200**:
```json
{
  "registration_id": "ec1a05b8-...",
  "embedding_job_id": "7e57c4d6-...",
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

**Errors**: `401` missing JWT · `500` DB/storage failure (safe to retry).

---

### 4.2 `POST /rescan/start`

**Auth**: required. **Body**:
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

**Errors**: `401` · `403` user doesn't own dog · `404` dog not found · `500`.

---

### 4.3 `POST /inference/start`

**Auth**: required. **Body**:
```json
{ "embedding_job_id": "uuid-returned-by-registration-or-rescan" }
```

**Response 200** (immediate):
```json
{ "embedding_job_id": "...", "status": "processing" }
```

Only call **after all 8 uploads are complete**.

**Errors**: `401` · `403` wrong user · `404` · `400` not pending · `409` concurrent call.

---

### 4.4 `GET /inference/status/{embedding_job_id}` — SSE

Pass JWT as query param — `EventSource` cannot set headers:
```
GET /inference/status/{id}?token=<supabase_access_token>
```

**Events:**

| Event | Terminal | Payload |
|---|---|---|
| `status` | No | `{ status: "processing", step: "quality_check" \| "generating_embedding" \| "duplicate_check" \| "saving_results" \| "starting" }` |
| `review_required` | **Yes** | `{ status: "possible_duplicate", candidates: DuplicateCandidate[] }` |
| `complete` | **Yes** | `{ status: "complete", dog_id, embedding_version }` |
| `error` | **Yes** | `{ status: "failed", reason, passed_count?, required?, notes? }` |

Call `es.close()` on every terminal event. `candidates` may be empty — human tap is still required.

---

### 4.5 `GET /inference/result/{embedding_job_id}` — polling fallback

**Auth**: `Authorization: Bearer` header. Use when SSE drops before terminal event.

**Response 200**:
```json
{
  "embedding_job_id": "...",
  "status": "complete | processing | failed",
  "registration_status": "possible_duplicate | completed | duplicate_confirmed | failed | processing",
  "intent": "enrollment | rescan",
  "dog_id": "uuid | null",
  "duplicate_found": false,
  "duplicate_dog_id": null,
  "match_score": null,
  "quality_passed": true,
  "quality_notes": { "crop_1": { "passed": true, "reason": "..." }, ... },
  "embedding_version": 1,
  "completed_at": "ISO8601 | null",
  "candidates": [...]
}
```

`candidates` only present when `registration_status === "possible_duplicate"`. Show review screen when you see it.

---

### 4.6 `POST /inference/{embedding_job_id}/resolve`

**Auth**: required.

**Body** (one of):
```json
{ "decision": "new" }
```
```json
{ "decision": "existing", "dog_id": "<uuid-of-matched-dog>" }
```

- `"new"` — creates dog + active embedding. Use when human taps "no match" or empty candidates.
- `"existing"` — links scan to matched dog. `dog_id` must be from the candidates list. Embedding saved as `is_active=false`.

**Response 200**:
```json
{
  "embedding_job_id": "...",
  "status": "complete",
  "registration_status": "completed | duplicate_confirmed",
  "dog_id": "uuid",
  "embedding_version": 1
}
```

**Errors**: `400` already resolved · `404` no registration · `409` concurrent resolve (`already_resolved`) · `422` bad decision/missing dog_id.

---

## 5. Uploading crops from iOS camera

```ts
import * as ImageManipulator from 'expo-image-manipulator'

async function ensureJpeg(uri: string): Promise<string> {
  const result = await ImageManipulator.manipulateAsync(
    uri, [],
    { compress: 0.92, format: ImageManipulator.SaveFormat.JPEG }
  )
  return result.uri
}

async function uploadCrop(presignedUrl: string, jpegUri: string): Promise<void> {
  // Convert local file URI to a fetch-compatible blob
  const response = await fetch(jpegUri)
  const blob = await response.blob()

  const res = await fetch(presignedUrl, {
    method: 'PUT',
    headers: { 'Content-Type': 'image/jpeg' },
    body: blob,
  })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
}

async function uploadAllCrops(
  presignedUrls: Array<{ index: number; url: string }>,
  cameraUris: string[]  // length 8, ordered 1..8
): Promise<void> {
  const jpegUris = await Promise.all(cameraUris.map(ensureJpeg))
  await Promise.all(
    presignedUrls.map((p) => uploadCrop(p.url, jpegUris[p.index - 1]))
  )
}
```

---

## 6. Reference TypeScript types

```ts
export interface PresignedUpload {
  index: number      // 1..8
  url: string
  path: string
  token: string
}

export interface RegistrationStartResponse {
  registration_id: string
  embedding_job_id: string
  presigned_urls: PresignedUpload[]
  expires_at: string
}

export interface RescanStartResponse {
  registration_id: string
  embedding_job_id: string
  presigned_urls: PresignedUpload[]
}

export interface InferenceStartResponse {
  embedding_job_id: string
  status: 'processing'
}

export interface DuplicateCandidate {
  dog_id: string
  name: string
  breed: string | null
  match_score: number | null
}

export type SseStatusEvent = {
  status: 'processing'
  step: 'starting' | 'quality_check' | 'generating_embedding' | 'duplicate_check' | 'saving_results'
}

export type SseReviewRequiredEvent = {
  status: 'possible_duplicate'
  candidates: DuplicateCandidate[]  // may be empty
}

export type SseCompleteEvent = {
  status: 'complete'
  dog_id: string
  embedding_version: number
}

export type SseErrorEvent = {
  status: 'failed'
  reason: string
  passed_count?: number
  required?: number
  notes?: Record<string, { passed: boolean; reason?: string }>
}

export interface ResolveRequest {
  decision: 'new' | 'existing'
  dog_id?: string  // required when decision === 'existing'
}

export interface ResolveResponse {
  embedding_job_id: string
  status: 'complete'
  registration_status: 'completed' | 'duplicate_confirmed'
  dog_id: string
  embedding_version: number
}

export interface ResultResponse {
  embedding_job_id: string
  status: 'complete' | 'processing' | 'failed'
  registration_status: string
  intent: 'enrollment' | 'rescan'
  dog_id: string | null
  duplicate_found: boolean | null
  duplicate_dog_id: string | null
  match_score: number | null
  quality_passed: boolean | null
  quality_notes: Record<string, { passed: boolean; reason?: string }> | null
  embedding_version: number | null
  completed_at: string | null
  candidates?: DuplicateCandidate[]  // only when registration_status === 'possible_duplicate'
}
```

---

## 7. Complete enrollment + resolve example

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
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

export async function enrollDog(
  cameraUris: string[],  // 8 URIs from iOS camera, order matches crop index 1..8
  onStep: (step: string) => void,
  onReviewRequired: (candidates: DuplicateCandidate[]) => void,
): Promise<string> {
  if (cameraUris.length !== 8) throw new Error('Need exactly 8 crops')

  // 1. start registration
  const reg: RegistrationStartResponse = await authedFetch(
    '/registration/start', { method: 'POST', body: '{}' }
  )

  // 2. convert to JPEG + upload in parallel
  await uploadAllCrops(reg.presigned_urls, cameraUris)

  // 3. kick off inference
  await authedFetch('/inference/start', {
    method: 'POST',
    body: JSON.stringify({ embedding_job_id: reg.embedding_job_id }),
  })

  // 4. SSE — wait for review_required
  const { data: { session } } = await supabase.auth.getSession()
  const es = new EventSource(
    `${API_BASE}/inference/status/${reg.embedding_job_id}?token=${session!.access_token}`
  )

  es.addEventListener('status', (e) => {
    const { step } = JSON.parse(e.data) as SseStatusEvent
    onStep(step)
  })

  es.addEventListener('review_required', (e) => {
    const { candidates } = JSON.parse(e.data) as SseReviewRequiredEvent
    es.close()
    onReviewRequired(candidates)
  })

  es.addEventListener('error', (e) => {
    es.close()
    const data = JSON.parse((e as MessageEvent).data || '{}') as SseErrorEvent
    throw new Error(data.reason || 'Pipeline failed')
  })

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) startPolling(reg.embedding_job_id, onReviewRequired)
  }

  return reg.embedding_job_id
}

// Called when human taps a button on the review screen
export async function resolveEnrollment(
  embeddingJobId: string,
  decision: 'new' | 'existing',
  dogId?: string,
): Promise<ResolveResponse> {
  return authedFetch(`/inference/${embeddingJobId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ decision, ...(dogId ? { dog_id: dogId } : {}) }),
  })
}
```

---

## 8. SSE + polling fallback

### SSE

```ts
const { data: { session } } = await supabase.auth.getSession()
const es = new EventSource(
  `${API_BASE}/inference/status/${embeddingJobId}?token=${session!.access_token}`
)

es.addEventListener('status', (e) => {
  const { step } = JSON.parse(e.data) as SseStatusEvent
  updateProgressUI(step)
})

es.addEventListener('review_required', (e) => {
  const { candidates } = JSON.parse(e.data) as SseReviewRequiredEvent
  es.close()
  showReviewScreen(candidates)  // candidates may be []
})

es.addEventListener('complete', (e) => {
  // rescan only
  const { dog_id, embedding_version } = JSON.parse(e.data) as SseCompleteEvent
  es.close()
  showSuccess(dog_id, embedding_version)
})

es.addEventListener('error', (e) => {
  const data = JSON.parse((e as MessageEvent).data || '{}') as SseErrorEvent
  es.close()
  showErrorScreen(data)
})

es.onerror = () => {
  if (es.readyState === EventSource.CLOSED) startPolling(embeddingJobId)
}
```

### Polling fallback

```ts
async function startPolling(
  embeddingJobId: string,
  onReviewRequired: (c: DuplicateCandidate[]) => void,
  maxMs = 60_000,
) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    const r: ResultResponse = await authedFetch(`/inference/result/${embeddingJobId}`)
    if (r.status === 'complete' && r.registration_status === 'possible_duplicate') {
      onReviewRequired(r.candidates ?? [])
      return
    }
    if (r.status === 'complete') { showSuccess(r.dog_id!, r.embedding_version!); return }
    if (r.status === 'failed') { showErrorScreen(r); return }
    await new Promise((res) => setTimeout(res, 2500))
  }
  throw new Error('poll timeout')
}
```

---

## 9. Error-handling checklist

| Case | What to do |
|---|---|
| `401` from any endpoint | `supabase.auth.refreshSession()`, retry once. Still failing → redirect to login. |
| `403` on `/rescan/start` | User doesn't own that dog. Log as bug — should not happen in correct UI. |
| `403` on `/inference/start` or `/resolve` | Same. Do not retry. |
| `404` on `/rescan/start` or `/inference/start` | Stale id. Re-fetch dog/job list and re-prompt. |
| `400` on `/inference/start` | Job already past pending. Skip to SSE/poll wait. |
| `409` on `/inference/start` | Concurrent call won race. Same as `400`. |
| `409` on `/resolve` (`already_resolved`) | Concurrent resolve won. Fetch `/inference/result/{id}` and show outcome. |
| `422` on `/resolve` | Bad decision value or missing `dog_id`. Fix request. |
| `error` SSE with `reason: "quality_check_failed"` | Crops too blurry, dark, or small. Show retry prompt with `notes` breakdown. |
| `error` SSE with `reason: "below_min_dim"` | Crop dimensions too small (< 224px). Ask user to re-capture closer to the nose. |
| `500` | Generic retry. `/registration/start` and `/rescan/start` are safe to retry. |
| Upload `PUT` fails | Retry that crop using the same presigned URL (valid 2h). |
| Registration expired (30 min) | Restart from `/registration/start`. |
| HEIC upload rejected | Run `ensureJpeg()` before upload (see §2). |

---

## 10. Key invariants

- Exactly **8 crops**, indexed 1..8. Match each to `presigned_urls[i].index`.
- All crops must be **JPEG**. Always run `ensureJpeg()` on iOS camera output before uploading.
- Set `Content-Type: image/jpeg` on every upload `PUT`.
- Never put `user_id` in request bodies — extracted from JWT only.
- Call `/inference/start` exactly once per `embedding_job_id`.
- Every enrollment ends at the review screen — there is no auto-enroll path.
- `review_required` is enrollment-only. Rescans emit `complete` directly.
- `candidates` in `review_required` may be empty — human tap still required.
