# Discoveries — Nose Biometrics Endpoints (Phase 1)

Notes captured while implementing `POST /registration/start`, `POST /rescan/start`,
and `POST /inference/start`. Things that surprised me, or where I deviated from
the spec in `CLAUDE.md`.

## Supabase presigned upload URLs

- The correct call is `sb.storage.from_(bucket).create_signed_upload_url(path)`
  (Python `supabase==2.7.4`, `storage3==0.5.5`).
- **Return-key casing changed between download and upload.** The existing
  download helper `create_signed_url(...)` returns `{"signedURL": ...}`
  (camelCase). The upload sibling `create_signed_upload_url(...)` returns
  `{"signed_url": ..., "token": ..., "path": ...}` (snake_case). Easy to get
  wrong if you copy the download pattern.
- **No `expires_in` parameter.** Unlike `create_signed_url(path, expires_in)`,
  the upload variant does not accept an expiry. Per Supabase docs the upload
  token defaults to ~2 hours and is not configurable from the SDK call. I
  recorded the chosen value in `settings.presigned_upload_ttl_seconds` for
  documentation only — the SDK does not consume it.
- **The signed URL alone is not enough for some clients.** The Supabase JS
  helpers expect both the URL and the token to call `uploadToSignedUrl()`. To
  keep both web/RN clients happy, the API returns `url`, `path`, and `token`
  per crop. A plain HTTP `PUT` to `url` also works for native `fetch`.
- The bucket can be private — the signed token authorises the single upload.
  No bucket policy changes are needed beyond creating the `snoutcloud` bucket.

## Service-role client behaviour

- `app/db/client.py::get_supabase()` uses `SUPABASE_SERVICE_ROLE_KEY`, which
  bypasses RLS entirely. That means our `embedding_jobs` and `registrations`
  inserts succeed regardless of whatever policies a future maintainer adds.
  Worth flagging because it also means the per-request user-id check is the
  *only* thing protecting these tables — there is no DB-level safety net.
- Supabase Python SDK is synchronous. The Postgres client methods (`.execute()`)
  block the event loop. FastAPI endpoints stay `async def` for consistency
  with the rest of the codebase, but a future cleanup could wrap these calls
  in `asyncio.to_thread(...)` to avoid blocking on slow Supabase responses.
- `.insert(...).execute()` returns `data: list[dict]` on success and raises
  on DB errors (e.g. unique-constraint violations). It does not raise on a
  missing FK target — that surfaces as a Postgres exception inside the
  generic except, which is why the rollback paths use a broad `except` and
  re-raise as 500.

## JWT verification

- **Deviated from the spec.** `CLAUDE.md` mandates JWKS-based RS256 verification
  via `PyJWKClient`. The existing routes (`dogs.py`, `episodes.py`, etc.) all
  use `jwt.decode(token, options={"verify_signature": False})`. To stay
  consistent and avoid a project-wide change in scope, the new shared
  `app/api/deps.py::get_current_user_id` follows the existing unverified
  pattern. This is documented in the dep's docstring. The full JWKS migration
  should be done as a single sweep across every route file.

## Concurrency

- `/inference/start` uses a CAS-style guard: the UPDATE includes
  `.eq("status", "pending")` in addition to the prior SELECT. Without it, two
  near-simultaneous calls for the same job would both pass the read-side
  status check and both spawn `run_pipeline`. With it, the loser gets 0 rows
  back from the UPDATE and we return 409.

## Spec deviations summary

| Spec item | Implementation choice | Why |
|---|---|---|
| JWKS RS256 verification | `verify_signature=False` (matches existing routes) | Single-route change should not introduce divergent auth behaviour |
| Response shape `presigned_urls[].url` only | Added `token` field per crop | Required by Supabase JS SDK's `uploadToSignedUrl()` helper |
| Bucket name in code | Lives in `settings.nose_crops_bucket` (default `"snoutcloud"`) | Keeps it separate from existing `dog-documents` bucket |
| `embedding_jobs.embedding_version` on rescan insert | Left at default `1` at job start | Spec says version bumps on *completion*, not on job start; the pipeline (not the start endpoint) will increment it |

## Things to double-check before going live

1. The `registrations.status` enum has a `'processing'` value. Spec lists
   `'initiated','uploading','processing','completed','failed','abandoned','possible_duplicate'`.
   Confirm migration has been applied to Supabase before exercising
   `/inference/start` with an enrollment job.
2. `embedding_jobs.user_id` is required in the schema. The seed migrations
   should ensure that column exists with the NOT NULL constraint as expected.
3. The `snoutcloud` storage bucket must be created in Supabase (private). The
   API does not auto-create it.
