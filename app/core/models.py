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
    user_id: UUID
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
    status: str                     # 'pending' | 'processing' | 'done' | 'failed'
    semantic_summary: str | None = None
    ocr_text: str | None = None
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


# ------------------------------------------------------------------
# AUTH & USER MODELS
# ------------------------------------------------------------------

class UserRole(str, Enum):
    VETERINARIAN = "veterinarian"
    GROOMER = "groomer"
    NUTRITIONIST = "nutritionist"
    TRAINER = "trainer"
    RESCUE_WORKER = "rescue_worker"
    OTHER = "other"


class UserExperienceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UserPrimaryUse(str, Enum):
    HEALTH_TRACKING = "health_tracking"
    OPERATIONS = "operations"
    CASUAL = "casual"


class UserDataSharingPreference(str, Enum):
    PRIVATE = "private"
    ORG = "org"
    PUBLIC = "public"


class UserOnboarding(BaseModel):
    """Onboarding data collected from user after OAuth."""
    is_owner: bool
    roles: list[UserRole]
    experience_level: UserExperienceLevel
    primary_use: UserPrimaryUse
    location_address: str
    location_lat: float
    location_lng: float
    org_name: str | None = None
    org_location_address: str | None = None
    org_location_lat: float | None = None
    org_location_lng: float | None = None
    data_sharing_preference: UserDataSharingPreference


class UserResponse(BaseModel):
    """Full user profile response."""
    id: UUID
    email: str
    is_owner: bool | None = None
    roles: list[str] = Field(default_factory=list)
    experience_level: str | None = None
    primary_use: str | None = None
    location_address: str | None = None
    location_lat: float | None = None
    location_lng: float | None = None
    org_name: str | None = None
    org_location_address: str | None = None
    org_location_lat: float | None = None
    org_location_lng: float | None = None
    data_sharing_preference: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    """Response after successful authentication."""
    status: str  # 'authenticated' | 'onboarding_required' | 'onboarding_complete'
    user_id: UUID
    email: str
    requires_onboarding: bool


class OnboardingCompleteResponse(BaseModel):
    """Response after onboarding completion."""
    status: str  # 'onboarding_complete'
    user: UserResponse


# ------------------------------------------------------------------
# EPISODE MODELS
# ------------------------------------------------------------------

class EpisodeCreate(BaseModel):
    """Create health episode for a dog."""
    dog_id: UUID
    label: str
    episode_type: EpisodeType
    started_on: date
    resolved_on: date | None = None
    is_ongoing: bool = False
    summary: str | None = None
    source: str | None = None  # 'document' | 'manual' | etc.


class EpisodeResponse(BaseModel):
    """Full episode details."""
    id: UUID
    dog_id: UUID
    label: str
    episode_type: str
    started_on: date
    resolved_on: date | None
    is_ongoing: bool
    summary: str | None
    source: str | None
    created_at: datetime


# ------------------------------------------------------------------
# GRAPH MODELS
# ------------------------------------------------------------------

class GraphEdgeCreate(BaseModel):
    """Create relationship in GraphRAG."""
    from_type: str  # 'entity' | 'episode' | 'dog' etc.
    from_id: UUID
    relation: str   # 'causedBy', 'partOf', 'relatedTo', etc.
    to_type: str
    to_id: UUID
    weight: float = 1.0
    source: str | None = None


class GraphEdgeResponse(BaseModel):
    """Graph edge details."""
    id: UUID
    from_type: str
    from_id: UUID
    relation: str
    to_type: str
    to_id: UUID
    weight: float
    source: str | None
    created_at: datetime


# ------------------------------------------------------------------
# SEARCH & QUERY MODELS
# ------------------------------------------------------------------

class SearchQuery(BaseModel):
    """Hybrid search query."""
    dog_id: UUID
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 10


class SearchResult(BaseModel):
    """Search result item."""
    entity_id: UUID | None = None
    entity_type: str | None = None
    score: float
    data: dict[str, Any]
    source: str  # 'vector' | 'structured'


# ------------------------------------------------------------------
# NOSE BIOMETRICS — EMBEDDING JOB MODELS
# ------------------------------------------------------------------

class EmbeddingJobIntent(str, Enum):
    ENROLLMENT = "enrollment"
    RESCAN     = "rescan"


class EmbeddingJobStatus(str, Enum):
    PENDING    = "pending"
    UPLOADING  = "uploading"
    PROCESSING = "processing"
    COMPLETE   = "complete"
    FAILED     = "failed"


class PresignedUpload(BaseModel):
    index: int
    url: str
    path: str
    token: str


class RegistrationStartResponse(BaseModel):
    registration_id: UUID
    embedding_job_id: UUID
    presigned_urls: list[PresignedUpload]
    expires_at: datetime


class RescanStartRequest(BaseModel):
    dog_id: UUID


class RescanStartResponse(BaseModel):
    embedding_job_id: UUID
    presigned_urls: list[PresignedUpload]


class InferenceStartRequest(BaseModel):
    embedding_job_id: UUID


class InferenceStartResponse(BaseModel):
    embedding_job_id: UUID
    status: str
