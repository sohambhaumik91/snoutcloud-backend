import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from uuid import UUID

from app.db.client import get_supabase
from app.core.models import DocumentUploadResponse, DocType
from app.services.storage import build_storage_path, upload_file
from app.pipelines.ocr import ocr_and_classify
from app.pipelines.extraction import extract_entities
from app.pipelines.writer import write_entities, update_document_post_extraction
from app.pipelines.chunker import chunk_and_embed_entity, entity_to_text

router = APIRouter(prefix="/documents", tags=["documents"])


async def run_pipeline(
    document_id: UUID,
    dog_id: UUID,
    file_bytes: bytes,
    content_type: str
) -> None:
    """
    Full ingest pipeline — runs as a background task after upload.

    Steps:
    1. OCR + classify
    2. Extract entities
    3. Write entities to entity_store
    4. Chunk + embed each entity
    5. Update document row with summary + payload
    """
    sb = get_supabase()

    try:
        # mark as processing
        sb.table("documents").update({
            "ocr_status": "pending",
            "enrichment_status": "running"
        }).eq("id", str(document_id)).execute()

        # step 1 — OCR + classify
        ocr_text, doc_type, confidence = await ocr_and_classify(
            file_bytes, content_type
        )

        # step 2 — extract entities
        payload = await extract_entities(ocr_text, doc_type)

        # step 3 — write entities
        entity_ids = await write_entities(payload, document_id, dog_id)

        # step 4 — chunk + embed each entity
        for entity_id, entity in zip(entity_ids, payload.entities):
            text = entity_to_text(entity.entity_type.value, entity.data)
            await chunk_and_embed_entity(entity_id, document_id, text)

        # step 5 — update document
        await update_document_post_extraction(document_id, payload, ocr_text)

    except Exception as e:
        sb.table("documents").update({
            "ocr_status": "failed",
            "enrichment_status": "failed"
        }).eq("id", str(document_id)).execute()
        raise e


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    dog_id: UUID = Form(...),
    owner_id: UUID = Form(...),
    file: UploadFile = File(...)
):
    """
    Upload a document for a dog.
    Immediately returns document_id and status.
    Pipeline runs in the background.
    """
    sb = get_supabase()

    # validate dog belongs to owner
    dog = sb.table("dogs")\
        .select("id")\
        .eq("id", str(dog_id))\
        .eq("owner_id", str(owner_id))\
        .single()\
        .execute()

    if not dog.data:
        raise HTTPException(status_code=404, detail="Dog not found")

    # read file
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    document_id = uuid.uuid4()

    # build storage path
    storage_path = build_storage_path(
        owner_id, dog_id, document_id, file.filename or "upload"
    )

    # upload to Supabase Storage
    await upload_file(file_bytes, storage_path, content_type)

    # create document row — status pending
    sb.table("documents").insert({
        "id":           str(document_id),
        "dog_id":       str(dog_id),
        "file_path":    storage_path,
        "file_type":    content_type,
        "original_name": file.filename,
        "doc_type":     "unknown",
        "ocr_status":   "pending",
        "enrichment_status": "pending"
    }).execute()

    # kick off pipeline in background
    background_tasks.add_task(
        run_pipeline,
        document_id,
        dog_id,
        file_bytes,
        content_type
    )

    return DocumentUploadResponse(
        document_id=document_id,
        dog_id=dog_id,
        file_path=storage_path,
        doc_type=DocType.UNKNOWN,
        doc_type_confidence=0.0,
        status="processing",
        clarification_questions=[]
    )


@router.get("/{document_id}/status")
async def get_document_status(document_id: UUID):
    """Poll pipeline status for a document."""
    sb = get_supabase()
    doc = sb.table("documents")\
        .select("id, doc_type, doc_type_confidence, ocr_status, enrichment_status, semantic_summary")\
        .eq("id", str(document_id))\
        .single()\
        .execute()

    if not doc.data:
        raise HTTPException(status_code=404, detail="Document not found")

    return doc.data
