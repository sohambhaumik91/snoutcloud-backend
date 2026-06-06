-- Human-in-the-loop dedup: park the computed embedding between scan and the user tap.
--
-- Run each statement SEPARATELY in the Supabase SQL editor.
-- ALTER TYPE … ADD VALUE cannot run in the same transaction as other DDL.

-- Step 1: add pending_embedding column (nullable; holds the vector until the user resolves)
ALTER TABLE public.embedding_jobs
  ADD COLUMN IF NOT EXISTS pending_embedding vector(384);

-- Step 2: add duplicate_confirmed to registration_status enum
ALTER TYPE public.registration_status ADD VALUE IF NOT EXISTS 'duplicate_confirmed';
