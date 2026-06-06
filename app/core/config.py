from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str

    anthropic_api_key: str = ""   # not used in this service; optional so Railway env stays clean

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

    # Nose biometric encoder checkpoint (~380MB).
    #
    # Baked into the Docker image at build time (too large for git; exceeds
    # Supabase Storage's 50MB upload cap). The worker loads it from this path.
    #   - In the container, docker-compose sets NOSE_MODEL_PATH to the baked
    #     location: /app/models/best_supcon_clahe_gem.pt
    #   - The default below is a local-dev convenience pointing at the training
    #     repo copy; override NOSE_MODEL_PATH to wherever your local .pt lives.
    nose_model_path: str = "../best_supcon_clahe_gem.pt"

    # ── Duplicate-suggestion thresholds ────────────────────────────────────
    # Cosine similarity floor to surface a candidate. Deliberately low (the safety
    # lever) — the human tap is the real gate, not this number.
    duplicate_suggest_threshold: float = 0.60   # env: DUPLICATE_SUGGEST_THRESHOLD
    # How many candidates to surface in the review screen.
    duplicate_candidate_count: int = 5           # env: DUPLICATE_CANDIDATE_COUNT

    # ── Quality gate thresholds (per nose crop) ─────────────────────────────
    # Defaults are the production-ish values; override in .env for permissive
    # testing (changing these needs only a container restart, not a rebuild).
    quality_min_dim: int = 224              # min width/height in px
    quality_min_sharpness: float = 50.0     # min variance-of-Laplacian
    quality_min_brightness: float = 20.0    # min mean luminance (0-255)
    quality_max_brightness: float = 235.0   # max mean luminance
    quality_min_passing_crops: int = 6      # min crops that must pass, of 8

    # ── Dev/test auth escape hatch ──────────────────────────────────────────
    # When set, requests with NO Bearer token fall back to this user_id (must
    # exist in public.users to satisfy FKs). Lets the test client run auth-less
    # before Google sign-in is wired. MUST be empty in production — a non-empty
    # value disables auth for unauthenticated requests.
    dev_auth_user_id: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False
        # .env is shared with docker-compose (e.g. NGROK_AUTHTOKEN for the ngrok
        # service); ignore keys this app doesn't model rather than erroring.
        extra = "ignore"


settings = Settings()
