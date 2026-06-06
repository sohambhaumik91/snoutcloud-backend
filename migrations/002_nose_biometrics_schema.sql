/**
 * Migration: dog_embeddings table
 *
 * `embedding_jobs`, `registrations`, and the related enums already exist in
 * the database. The only missing piece for nose-print matching is the
 * embedding store itself.
 *
 * Adds:
 *   - dog_embeddings table (VECTOR(384), versioned, is_active flag)
 *   - ivfflat cosine index for ANN duplicate search
 *   - partial index on dog_id WHERE is_active = true
 *
 * pgvector is assumed enabled (chunks.embedding already uses it). The
 * CREATE EXTENSION below is a no-op if so.
 *
 * Embedding dim is 384 — matches the trained NoseEncoder. The "VECTOR(512)"
 * in CLAUDE.md is stale doc.
 *
 * Idempotent: safe to re-run.
 */

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.dog_embeddings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dog_id              UUID NOT NULL REFERENCES public.dogs(id) ON DELETE CASCADE,
  embedding_job_id    UUID NOT NULL REFERENCES public.embedding_jobs(id) ON DELETE CASCADE,
  embedding           VECTOR(384) NOT NULL,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ivfflat cosine ANN. `lists=100` is a starting point; tune toward
-- sqrt(rowcount) once you have meaningful volume.
CREATE INDEX IF NOT EXISTS idx_dog_embeddings_cosine
  ON public.dog_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Only active rows are ever queried for matching.
CREATE INDEX IF NOT EXISTS idx_dog_embeddings_active
  ON public.dog_embeddings (dog_id)
  WHERE is_active = TRUE;
