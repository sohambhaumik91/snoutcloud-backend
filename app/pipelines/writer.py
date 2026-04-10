from uuid import UUID, uuid4
from app.db.client import get_supabase
from app.core.models import ExtractedEntity, ExtractionPayload, EntityType


async def write_entities(
    payload: ExtractionPayload,
    document_id: UUID,
    dog_id: UUID
) -> list[UUID]:
    """
    Write all extracted entities to entity_store.
    Returns list of created entity IDs.
    """
    sb = get_supabase()
    entity_ids = []

    for entity in payload.entities:
        entity_id = uuid4()

        row = {
            "id":               str(entity_id),
            "dog_id":           str(dog_id),
            "document_id":      str(document_id),
            "entity_type":      entity.entity_type.value,
            "entity_subtype":   entity.entity_subtype,
            "occurred_on_year":  entity.temporal.year,
            "occurred_on_month": entity.temporal.month,
            "occurred_on_day":   entity.temporal.day,
            "temporal_precision": entity.temporal.precision.value,
            "period_start":     entity.period_start.isoformat()
                                if entity.period_start else None,
            "period_end":       entity.period_end.isoformat()
                                if entity.period_end else None,
            "location_name":    entity.location_name,
            "location_lat":     entity.location_lat,
            "location_lng":     entity.location_lng,
            "data":             entity.data,
            "confidence":       entity.confidence,
            "extraction_model": "claude-opus-4-6",
            "source":           "llm",
            # flag for review if confidence is low or entity type is unknown
            "review_flag":      entity.confidence < 0.7
                                or entity.entity_type == EntityType.UNKNOWN,
            "review_reason":    "low confidence" if entity.confidence < 0.7
                                else ("unknown entity type"
                                      if entity.entity_type == EntityType.UNKNOWN
                                      else None)
        }

        sb.table("entity_store").insert(row).execute()
        entity_ids.append(entity_id)

    return entity_ids


async def update_document_post_extraction(
    document_id: UUID,
    payload: ExtractionPayload,
    ocr_text: str
) -> None:
    """Update the document row with OCR text, summary, and payload."""
    sb = get_supabase()

    sb.table("documents").update({
        "ocr_raw_text":       ocr_text,
        "semantic_summary":   payload.semantic_summary,
        "extraction_payload": payload.model_dump(),
        "doc_type":           payload.doc_type.value,
        "doc_type_confidence": payload.doc_type_confidence,
        "ocr_status":         "done",
        "enrichment_status":  "done"
    }).eq("id", str(document_id)).execute()
