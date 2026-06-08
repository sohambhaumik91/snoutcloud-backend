# Dog Profile Endpoints

Base URL: `https://snoutcloud-backend-production.up.railway.app`
Auth: `Authorization: Bearer <supabase_access_token>` on all endpoints.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/get-pack` | List all dogs for authenticated user |
| `GET` | `/dogs/{dog_id}` | Get full profile for a single dog |
| `PUT` | `/dogs/{dog_id}/profile` | Create or update dog profile metadata |
| `POST` | `/dogs/{dog_id}/photos` | Upload a photo to a slot (main / secondary_1 / secondary_2) |
| `DELETE` | `/dogs/{dog_id}/photos/{slot}` | Remove a photo from a slot |

---

## GET /get-pack

Returns lightweight summary of all user's dogs. Use on the Pack screen.

**Response:**
```json
{
  "dogs": [
    {
      "dog_id": "uuid",
      "name": "Biscuit",
      "breed": "Golden Retriever",
      "sex": "M",
      "color": "Tan",
      "photo_url": "https://...main.jpg",
      "created_at": "ISO8601"
    }
  ]
}
```
`photo_url` is the main slot photo URL or `null`.

---

## GET /dogs/{dog_id}

Full profile including all photo slots and embedding version.

**Response:**
```json
{
  "dog_id": "uuid",
  "name": "Biscuit",
  "breed": "Golden Retriever",
  "sex": "M",
  "age_text": "2 years",
  "color": "Tan",
  "bio": "Loves belly rubs.",
  "photos": {
    "main": "https://...main.jpg",
    "secondary_1": "https://...secondary_1.jpg",
    "secondary_2": null
  },
  "embedding_version": 1,
  "owner_id": "uuid",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

---

## PUT /dogs/{dog_id}/profile

Update profile metadata. Send as `multipart/form-data`.

**Fields:**
- `name` (required, non-empty)
- `breed`, `sex`, `age_text`, `color`, `bio` (all optional)

**Response:** updated dog fields.

---

## POST /dogs/{dog_id}/photos

Upload a photo. Send as `multipart/form-data`.

**Fields:**
- `photo` — image file (JPEG/PNG/WebP, max 5MB)
- `slot` — `"main"` | `"secondary_1"` | `"secondary_2"`

Replaces any existing photo in the same slot.

**Response:**
```json
{
  "photo_id": "uuid",
  "dog_id": "uuid",
  "slot": "main",
  "url": "https://...main.jpg",
  "created_at": "ISO8601"
}
```

---

## DELETE /dogs/{dog_id}/photos/{slot}

Remove a photo from a slot.

**Response:**
```json
{ "deleted": true, "slot": "main" }
```

---

## Migration to run in Supabase

Run `migrations/005_dog_profile_photos.sql` in the Supabase SQL editor.
This adds profile columns to `dogs`, creates the `dog_photos` table, and
creates the `dog-photos` storage bucket.

---

## App integration flow

1. After `/resolve` returns `dog_id`, navigate to Create Dog Profile screen
2. User fills in name/breed/etc and selects up to 3 photos
3. Upload photos in parallel: `POST /dogs/{dog_id}/photos` (once per slot)
4. Save metadata: `PUT /dogs/{dog_id}/profile`
5. Navigate to Dog Profile screen — call `GET /dogs/{dog_id}` on mount
6. Pack screen — call `GET /get-pack` on mount
