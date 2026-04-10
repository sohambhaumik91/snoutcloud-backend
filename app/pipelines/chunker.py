from uuid import UUID, uuid4
from app.db.client import get_supabase
from app.services.embedding import embed_batch

CHUNK_SIZE = 400        # characters per chunk
CHUNK_OVERLAP = 80      # overlap between chunks


def split_text(text: str) -> list[str]:
    """Split text into overlapping chunks."""
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    text = text.strip()

    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


async def chunk_and_embed_entity(
    entity_id: UUID,
    document_id: UUID,
    text: str
) -> None:
    """
    Chunk an entity's text, embed all chunks, write to chunks table.
    Text is constructed from the entity's data fields.
    """
    sb = get_supabase()
    chunks = split_text(text)

    if not chunks:
        return

    # embed all chunks in one batch call
    embeddings = await embed_batch(chunks)

    rows = []
    for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        rows.append({
            "id":               str(uuid4()),
            "document_id":      str(document_id),
            "entity_id":        str(entity_id),
            "chunk_index":      idx,
            "text":             chunk_text,
            "embedding":        embedding,
            "embedding_model":  "text-embedding-3-small"
        })

    # batch insert
    sb.table("chunks").insert(rows).execute()


def entity_to_text(entity_type: str, data: dict) -> str:
    """
    Convert an entity's data blob to a natural language string for embedding.
    The text should be semantically rich — this is what vector search queries against.
    """
    if entity_type == "prescription":
        parts = [
            f"Prescription for {data.get('drug_name', 'unknown drug')}",
            f"dosage {data.get('dosage_mg', '')} {data.get('dosage_unit', '')}",
            f"frequency {data.get('frequency', '')}",
            f"duration {data.get('duration_days', '')} days",
            f"route {data.get('route', '')}",
        ]
        if data.get("diagnosed_for"):
            parts.append(f"prescribed for {data['diagnosed_for']}")
        if data.get("vet_name"):
            parts.append(f"prescribed by {data['vet_name']}")
        if data.get("symptoms_noted"):
            parts.append(f"symptoms: {', '.join(data['symptoms_noted'])}")
        return ". ".join(p for p in parts if p.strip(". "))

    elif entity_type == "vaccine":
        parts = [
            f"Vaccine {data.get('vaccine_name', 'unknown')}",
            f"manufacturer {data.get('manufacturer', '')}",
        ]
        if data.get("next_due"):
            parts.append(f"next due {data['next_due']}")
        if data.get("vet_name"):
            parts.append(f"administered by {data['vet_name']}")
        return ". ".join(p for p in parts if p.strip(". "))

    elif entity_type == "lab_result":
        parts = [
            f"Lab result {data.get('test_name', '')} {data.get('parameter', '')}",
            f"value {data.get('value', '')} {data.get('unit', '')}",
            f"status {data.get('flag', 'unknown')}",
        ]
        if data.get("lab_name"):
            parts.append(f"from {data['lab_name']}")
        return ". ".join(p for p in parts if p.strip(". "))

    elif entity_type == "observation":
        observations = data.get("observations", [])
        obs_text = ". ".join(
            f"{o.get('symptom', '')} {o.get('detail', '')}"
            for o in observations
        )
        raw = data.get("raw_note", "")
        return f"Owner observation: {obs_text}. {raw}".strip()

    elif entity_type == "travel_event":
        parts = [
            f"Travel to {data.get('destination', 'unknown destination')}",
            f"by {data.get('transport', 'unknown transport')}",
        ]
        if data.get("first_time"):
            parts.append("first time visiting this destination")
        if data.get("notes"):
            parts.append(data["notes"])
        return ". ".join(p for p in parts if p.strip(". "))

    else:
        # generic fallback — dump all string values from data
        parts = [f"{k}: {v}" for k, v in data.items()
                 if isinstance(v, (str, int, float)) and v]
        return f"{entity_type}. " + ". ".join(parts)
