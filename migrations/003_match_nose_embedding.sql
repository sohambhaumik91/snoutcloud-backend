-- pgvector RPC for nearest-neighbour duplicate check on enrollment.
-- Called from app/pipelines/nose_pipeline.py via sb.rpc("match_nose_embedding", ...).
--
-- Returns rows where cosine similarity (1 - cosine_distance) >= match_threshold,
-- ordered by closest first, capped at match_count.

CREATE OR REPLACE FUNCTION public.match_nose_embedding(
    query_embedding vector,
    match_threshold float,
    match_count int DEFAULT 1
)
RETURNS TABLE (
    embedding_id uuid,
    dog_id uuid,
    embedding_job_id uuid,
    similarity float
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        e.id          AS embedding_id,
        e.dog_id      AS dog_id,
        e.embedding_job_id,
        1 - (e.embedding <=> query_embedding) AS similarity
    FROM public.dog_embeddings e
    WHERE e.is_active = TRUE
      AND 1 - (e.embedding <=> query_embedding) >= match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
$$;
