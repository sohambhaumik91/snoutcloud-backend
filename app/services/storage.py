import mimetypes
from uuid import UUID
from app.db.client import get_supabase
from app.core.config import settings


def build_storage_path(owner_id: UUID, dog_id: UUID, document_id: UUID,
                        original_name: str) -> str:
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
    return f"{owner_id}/dogs/{dog_id}/{document_id}.{ext}"


async def upload_file(
    file_bytes: bytes,
    path: str,
    content_type: str
) -> str:
    """Upload file to Supabase Storage. Returns the storage path."""
    sb = get_supabase()
    sb.storage.from_(settings.storage_bucket).upload(
        path=path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"}
    )
    return path


async def get_signed_url(path: str, expires_in: int = 3600) -> str:
    """Get a temporary signed URL for a stored file."""
    sb = get_supabase()
    response = sb.storage.from_(settings.storage_bucket).create_signed_url(
        path=path,
        expires_in=expires_in
    )
    return response["signedURL"]


async def download_file(path: str) -> bytes:
    """Download file bytes — used by the OCR pipeline."""
    sb = get_supabase()
    return sb.storage.from_(settings.storage_bucket).download(path)
