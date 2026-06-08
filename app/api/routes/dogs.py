"""Dog profile routes — profile metadata, photos, pack listing."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import get_current_user_id
from app.db.client import get_supabase

router = APIRouter(tags=["dogs"])

PHOTO_BUCKET = "dog-photos"
VALID_SLOTS = ("main", "secondary_1", "secondary_2")
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5MB
VALID_CONTENT_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")


def _own_dog_or_raise(sb, dog_id: str, user_id: str) -> dict:
    """Fetch a dog row and 403 if the caller doesn't own it."""
    res = sb.table("dogs").select("id, user_id").eq("id", dog_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Dog not found")
    dog = res.data[0]
    if dog["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You do not own this dog")
    return dog


def _photo_public_url(sb, storage_path: str) -> str:
    """Return a public URL for a dog-photos storage path."""
    return sb.storage.from_(PHOTO_BUCKET).get_public_url(storage_path)


def _dog_photos(sb, dog_id: str) -> dict:
    """Return { slot: url } for all photo slots, None if slot not uploaded."""
    res = sb.table("dog_photos").select("slot, storage_path").eq("dog_id", dog_id).execute()
    slot_map = {row["slot"]: _photo_public_url(sb, row["storage_path"]) for row in (res.data or [])}
    return {
        "main": slot_map.get("main"),
        "secondary_1": slot_map.get("secondary_1"),
        "secondary_2": slot_map.get("secondary_2"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /dogs — list all dogs for authenticated user (Pack screen)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/get-pack")
async def get_pack(user_id: UUID = Depends(get_current_user_id)) -> dict:
    """Return a lightweight summary of all dogs owned by the authenticated user."""
    sb = get_supabase()
    try:
        res = (
            sb.table("dogs")
            .select("id, name, breed, sex, color, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dogs: {e}")

    dogs = []
    for row in (res.data or []):
        photo_res = (
            sb.table("dog_photos")
            .select("storage_path")
            .eq("dog_id", row["id"])
            .eq("slot", "main")
            .limit(1)
            .execute()
        )
        photo_url = None
        if photo_res.data:
            photo_url = _photo_public_url(sb, photo_res.data[0]["storage_path"])
        dogs.append({
            "dog_id": row["id"],
            "name": row.get("name"),
            "breed": row.get("breed"),
            "sex": row.get("sex"),
            "color": row.get("color"),
            "photo_url": photo_url,
            "created_at": row.get("created_at"),
        })

    return {"dogs": dogs}


# ─────────────────────────────────────────────────────────────────────────────
# GET /dogs/{dog_id} — full dog profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dogs/{dog_id}")
async def get_dog(dog_id: UUID, user_id: UUID = Depends(get_current_user_id)) -> dict:
    """Return the full profile for a single dog."""
    sb = get_supabase()
    res = (
        sb.table("dogs")
        .select("id, user_id, name, breed, sex, age_text, color, bio, created_at, updated_at")
        .eq("id", str(dog_id))
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Dog not found")
    dog = res.data[0]
    if dog["user_id"] != str(user_id):
        raise HTTPException(status_code=403, detail="You do not own this dog")

    # Fetch latest active embedding version
    emb_res = (
        sb.table("dog_embeddings")
        .select("embedding_job_id")
        .eq("dog_id", str(dog_id))
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    embedding_version = None
    if emb_res.data:
        job_res = (
            sb.table("embedding_jobs")
            .select("embedding_version")
            .eq("id", emb_res.data[0]["embedding_job_id"])
            .limit(1)
            .execute()
        )
        if job_res.data:
            embedding_version = job_res.data[0].get("embedding_version")

    return {
        "dog_id": dog["id"],
        "name": dog.get("name"),
        "breed": dog.get("breed"),
        "sex": dog.get("sex"),
        "age_text": dog.get("age_text"),
        "color": dog.get("color"),
        "bio": dog.get("bio"),
        "photos": _dog_photos(sb, str(dog_id)),
        "embedding_version": embedding_version,
        "owner_id": dog["user_id"],
        "created_at": dog.get("created_at"),
        "updated_at": dog.get("updated_at"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUT /dogs/{dog_id}/profile — update profile metadata
# ─────────────────────────────────────────────────────────────────────────────

@router.put("/dogs/{dog_id}/profile")
async def update_profile(
    dog_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    name: str = Form(...),
    breed: str | None = Form(None),
    sex: str | None = Form(None),
    age_text: str | None = Form(None),
    color: str | None = Form(None),
    bio: str | None = Form(None),
) -> dict:
    """Update profile metadata for a dog."""
    if not name.strip():
        raise HTTPException(status_code=422, detail="name must not be empty")

    sb = get_supabase()
    _own_dog_or_raise(sb, str(dog_id), str(user_id))

    now_iso = datetime.now(timezone.utc).isoformat()
    update_data: dict = {"name": name.strip(), "updated_at": now_iso}
    if breed is not None:
        update_data["breed"] = breed
    if sex is not None:
        update_data["sex"] = sex
    if age_text is not None:
        update_data["age_text"] = age_text
    if color is not None:
        update_data["color"] = color
    if bio is not None:
        update_data["bio"] = bio

    try:
        res = (
            sb.table("dogs")
            .update(update_data)
            .eq("id", str(dog_id))
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {e}")

    row = res.data[0] if res.data else {}
    return {
        "dog_id": str(dog_id),
        "name": row.get("name"),
        "breed": row.get("breed"),
        "sex": row.get("sex"),
        "age_text": row.get("age_text"),
        "color": row.get("color"),
        "bio": row.get("bio"),
        "updated_at": row.get("updated_at"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /dogs/{dog_id}/photos — upload a photo to a slot
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/dogs/{dog_id}/photos")
async def upload_photo(
    dog_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    slot: str = Form(...),
    photo: UploadFile = File(...),
) -> dict:
    """Upload a photo for a dog. Replaces any existing photo in the same slot."""
    if slot not in VALID_SLOTS:
        raise HTTPException(status_code=422, detail=f"slot must be one of: {', '.join(VALID_SLOTS)}")

    content_type = photo.content_type or ""
    if content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported media type: {content_type}. Use JPEG, PNG, or WebP.")

    data = await photo.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum 5MB.")

    sb = get_supabase()
    _own_dog_or_raise(sb, str(dog_id), str(user_id))

    ext = content_type.split("/")[-1].replace("jpeg", "jpg")
    storage_path = f"{dog_id}/{slot}.{ext}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Delete existing file in storage if replacing
    existing = (
        sb.table("dog_photos")
        .select("storage_path")
        .eq("dog_id", str(dog_id))
        .eq("slot", slot)
        .limit(1)
        .execute()
    )
    if existing.data:
        try:
            sb.storage.from_(PHOTO_BUCKET).remove([existing.data[0]["storage_path"]])
        except Exception:
            pass

    # Upload new file
    try:
        sb.storage.from_(PHOTO_BUCKET).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {e}")

    # Upsert dog_photos row
    photo_id = str(uuid4())
    try:
        sb.table("dog_photos").upsert({
            "id": photo_id,
            "dog_id": str(dog_id),
            "slot": slot,
            "storage_path": storage_path,
            "url": _photo_public_url(sb, storage_path),
            "created_at": now_iso,
        }, on_conflict="dog_id,slot").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save photo record: {e}")

    return {
        "photo_id": photo_id,
        "dog_id": str(dog_id),
        "slot": slot,
        "url": _photo_public_url(sb, storage_path),
        "created_at": now_iso,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /dogs/{dog_id}/photos/{slot} — remove a photo slot
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/dogs/{dog_id}/photos/{slot}")
async def delete_photo(
    dog_id: UUID,
    slot: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Delete a photo from a specific slot."""
    if slot not in VALID_SLOTS:
        raise HTTPException(status_code=422, detail=f"slot must be one of: {', '.join(VALID_SLOTS)}")

    sb = get_supabase()
    _own_dog_or_raise(sb, str(dog_id), str(user_id))

    existing = (
        sb.table("dog_photos")
        .select("storage_path")
        .eq("dog_id", str(dog_id))
        .eq("slot", slot)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"No photo in slot '{slot}'")

    try:
        sb.storage.from_(PHOTO_BUCKET).remove([existing.data[0]["storage_path"]])
    except Exception:
        pass

    sb.table("dog_photos").delete().eq("dog_id", str(dog_id)).eq("slot", slot).execute()

    return {"deleted": True, "slot": slot}
