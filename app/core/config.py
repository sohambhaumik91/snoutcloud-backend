from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str

    anthropic_api_key: str

    storage_bucket: str = "dog-documents"
    nose_crops_bucket: str = "dog_nose_crops"
    # RLS policy on the dog_nose_crops bucket requires every object key to
    # live under this top-level folder: (storage.foldername(name))[1] = 'private'
    nose_crops_prefix: str = "private"
    embedding_dimensions: int = 384

    # presigned upload URLs: per Supabase, signed upload URLs expire ~2 hours
    # by default and the duration is not configurable on the SDK call.
    presigned_upload_ttl_seconds: int = 7200
    registration_ttl_seconds: int = 1800   # 30 minutes

    redis_url: str = "redis://localhost:6379"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
