# PawLog — Claude Code Context

## What this is
A dog health memory platform. FastAPI backend with Supabase (Postgres + pgvector).
Ingests vet documents (prescriptions, vaccines, lab reports, notes) via Claude Vision OCR,
extracts structured entities, stores them in a GraphRAG schema, and enables
natural language querying over a dog's complete health history.

## Stack
- Python 3.12 + FastAPI + uvicorn
- Supabase (Postgres + pgvector + Storage)
- Anthropic API (claude-opus-4-6 for OCR + extraction)
- OpenAI API (text-embedding-3-small for embeddings)
- Pydantic v2 for models

## Project structure
```
pawlog/
  app/
    main.py                   # FastAPI app, router registration
    core/
      config.py               # pydantic-settings, reads .env
      models.py               # all Pydantic models + enums
    db/
      client.py               # Supabase client singleton (service role)
    pipelines/
      ocr.py                  # Claude Vision OCR + doc classification
      extraction.py           # LLM entity extraction per doc_type
      writer.py               # writes entities to entity_store
      chunker.py              # text chunking + embedding write
    services/
      storage.py              # Supabase Storage upload/download
      embedding.py            # OpenAI embedding calls
    api/
      routes/
        documents.py          # upload endpoint + pipeline orchestration
  tests/
  requirements.txt
  .env                        # never commit this
  .env.example
```

## Environment setup
```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# fill in .env with real keys
```

## Running the server
```bash
uvicorn app.main:app --reload
# runs on http://localhost:8000
# docs at http://localhost:8000/docs
```

## Running tests
```bash
pytest tests/ -v
```

## Environment variables required
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- SUPABASE_ANON_KEY
- ANTHROPIC_API_KEY
- OPENAI_API_KEY

## Key design decisions

### Entity store pattern
All extracted entities go into a single `entity_store` table regardless of doc type.
Common queryable fields (temporal, location, entity_type) are atomic columns.
Entity-specific fields live in a `data` JSONB column.
Do not create new tables for new entity types — extend entity_store.

### Ingest pipeline order
upload → storage → OCR (ocr.py) → extraction (extraction.py) →
write entities (writer.py) → chunk + embed (chunker.py) → update document row

### Hybrid search
Two legs merged with Reciprocal Rank Fusion (RRF):
1. Vector: `match_chunks` Supabase RPC using pgvector cosine similarity
2. Structured: direct entity_store query filtered by entity_type + temporal fields

### Doc types supported
prescription, vaccine_certificate, lab_report, discharge_summary,
observation_note, travel_note, excel_sheet, grooming_record, unknown

### Entity types
prescription, vaccine, lab_result, observation, travel_event,
diet_entry, weight_entry, procedure, media_moment, grooming_record, unknown

## Current status
- [x] Schema on Supabase (10 tables + match_chunks RPC)
- [x] FastAPI scaffold
- [x] Upload endpoint (POST /documents/upload)
- [x] Status endpoint (GET /documents/{id}/status)
- [x] OCR + classification pipeline
- [x] Entity extraction pipeline
- [x] Entity writer
- [x] Chunker + embedder
- [ ] Conversation endpoint (next)
- [ ] Context assembler
- [ ] Hybrid search implementation
- [ ] Dogs CRUD endpoints
- [ ] Owners endpoint

## What to build next
Conversation endpoint:
  POST /conversations          — create conversation for a dog
  POST /conversations/message  — send message, get context-grounded response
  GET  /conversations/{id}     — get conversation history

The conversation endpoint needs:
1. Intent agent — classify query intent + extract temporal/entity filters
2. Hybrid search — vector + structured, merged with RRF
3. Context assembler — build context packet from search results
4. LLM call — Claude with full context packet as system context
5. Turn persistence — write turn to conversation_turns with retrieved entity IDs

## Supabase schema notes
- All tables have RLS enabled
- Use service role client (get_supabase()) for all server-side writes
- Storage bucket: dog-documents
- Storage path pattern: {owner_id}/dogs/{dog_id}/{document_id}.{ext}
- match_chunks RPC available for vector search
- HNSW index on chunks.embedding — build after first data load

## Debugging tips
- Check ocr_status and enrichment_status on documents table first
- entity_store rows with review_flag=true need human confirmation
- extraction_payload on documents stores the full raw LLM JSON for reprocessing
- If embedding fails, chunks table will be empty for that entity
