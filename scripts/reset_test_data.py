"""Wipe nose-biometrics test data: registrations + embedding_jobs (and the rows
that FK into them).

Handles the circular FK (registrations.embedding_job_id <-> embedding_jobs.
registration_id, both RESTRICT) and cascades:
  - deleting registrations  cascades nose_scan_frames
  - deleting embedding_jobs cascades dog_embeddings

By default it leaves the `dogs` table alone (those rows become embedding-less).
Pass --dogs to ALSO delete every row in dogs (use only if dogs holds test data).

Usage:
  python scripts/reset_test_data.py          # wipe registrations + embedding_jobs (+cascades)
  python scripts/reset_test_data.py --dogs   # also wipe dogs
"""

import sys

from app.db.client import get_supabase

NIL = "00000000-0000-0000-0000-000000000000"  # filter that matches every real UUID
WITH_DOGS = "--dogs" in sys.argv


def count(table: str, pk: str) -> int:
    return len(get_supabase().table(table).select(pk).limit(100000).execute().data)


def snapshot(label: str) -> None:
    sb_counts = {
        "embedding_jobs": count("embedding_jobs", "id"),
        "registrations": count("registrations", "registration_id"),
        "nose_scan_frames": count("nose_scan_frames", "frame_id"),
        "dog_embeddings": count("dog_embeddings", "id"),
        "dogs": count("dogs", "id"),
    }
    print(f"{label}: " + "  ".join(f"{k}={v}" for k, v in sb_counts.items()))


def main() -> int:
    sb = get_supabase()
    snapshot("before")

    # 1. break the circular FK so registrations can be deleted
    sb.table("embedding_jobs").update({"registration_id": None}).neq("id", NIL).execute()
    # 2. delete registrations (cascades nose_scan_frames)
    sb.table("registrations").delete().neq("registration_id", NIL).execute()
    # 3. delete embedding_jobs (cascades dog_embeddings)
    sb.table("embedding_jobs").delete().neq("id", NIL).execute()

    if WITH_DOGS:
        sb.table("dogs").delete().neq("id", NIL).execute()

    snapshot("after ")
    print("done." + ("" if WITH_DOGS else "  (dogs left intact — pass --dogs to wipe them)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
