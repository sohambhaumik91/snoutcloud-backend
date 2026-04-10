from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str

    anthropic_api_key: str
    openai_api_key: str

    storage_bucket: str = "dog-documents"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
