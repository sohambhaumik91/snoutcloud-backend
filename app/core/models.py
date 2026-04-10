from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any
from uuid import UUID
from datetime import date, datetime
from enum import Enum


# ------------------------------------------------------------------
# ENUMS
# ------------------------------------------------------------------

class DocType(str, Enum):
    PRESCRIPTION        = "prescription"
    VACCINE_CERTIFICATE = "vaccine_certificate"
    LAB_REPORT          = "lab_report"
    DISCHARGE_SUMMARY   = "discharge_summary"
    OBSERVATION_NOTE    = "observation_note"
    TRAVEL_NOTE         = "travel_note"
    EXCEL_SHEET         = "excel_sheet"
    GROOMING_RECORD     = "grooming_record"
    UNKNOWN             = "unknown"


class EntityType(str, Enum):
    PRESCRIPTION    = "prescription"
    VACCINE         = "vaccine"
    LAB_RESULT      = "lab_result"
    OBSERVATION     = "observation"
    TRAVEL_EVENT    = "travel_event"
    DIET_ENTRY      = "diet_entry"
    WEIGHT_ENTRY    = "weight_entry"
    PROCEDURE       = "procedure"
    MEDIA_MOMENT    = "media_moment"
    GROOMING_RECORD = "grooming_record"
    UNKNOWN         = "unknown"


class TemporalPrecision(str, Enum):
    DAY         = "day"
    MONTH       = "month"
    YEAR        = "year"
    APPROXIMATE = "approximate"
    UNKNOWN     = "unknown"


class EpisodeType(str, Enum):
    ILLNESS     = "illness"
    INJURY      = "injury"
    SURGERY     = "surgery"
    ROUTINE     = "routine"
    TRAVEL      = "travel"
    BEHAVIOURAL = "behavioural"
    OTHER       = "other"


# ------------------------------------------------------------------
# REQUEST / RESPONSE MODELS
# ------------------------------------------------------------------

class DogCreate(BaseModel):
    name: str
    breed: str | None = None
    dob: date | None = None
    sex: str = "unknown"


class DogResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    breed: str | None
    dob: date | None
    sex: str
    created_at: datetime


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    dog_id: UUID
    file_path: str
    doc_type: DocType
    doc_type_confidence: float
    status: str                     # 'uploaded' | 'processing' | 'done'
    clarification_questions: list[str] = Field(default_factory=list)


class ClarificationAnswer(BaseModel):
    document_id: UUID
    answers: dict[str, str]         # question → answer


# ------------------------------------------------------------------
# EXTRACTION PIPELINE MODELS
# ------------------------------------------------------------------

class ExtractedTemporal(BaseModel):
    year: int | None = None
    month: int | None = None
    day: int | None = None
    precision: TemporalPrecision = TemporalPrecision.UNKNOWN


class ExtractedEntity(BaseModel):
    entity_type: EntityType
    entity_subtype: str | None = None
    temporal: ExtractedTemporal = Field(default_factory=ExtractedTemporal)
    period_start: date | None = None
    period_end: date | None = None
    location_name: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class ExtractionPayload(BaseModel):
    doc_type: DocType
    doc_type_confidence: float
    semantic_summary: str
    entities: list[ExtractedEntity]
    episode_hint: str | None = None     # suggested episode label if applicable
    clarification_questions: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# CONVERSATION MODELS
# ------------------------------------------------------------------

class ConversationCreate(BaseModel):
    dog_id: UUID


class MessageRequest(BaseModel):
    conversation_id: UUID
    content: str


class MessageResponse(BaseModel):
    conversation_id: UUID
    turn_id: UUID
    role: str
    content: str
    retrieved_entities: list[UUID] = Field(default_factory=list)
    retrieved_documents: list[UUID] = Field(default_factory=list)
    confirmed_absent: dict = Field(default_factory=dict)
