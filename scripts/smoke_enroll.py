"""End-to-end auth-less enrollment smoke test.

Drives the live backend exactly like the client will:
  registration/start  ->  PUT 8 crops to Supabase  ->  inference/start  ->  poll result

Proves the worker embeds the crops and creates a dogs row. Runs auth-less
(no Bearer token), so the backend must have DEV_AUTH_USER_ID set.

Usage:
  python scripts/smoke_enroll.py                 # hits http://localhost:8000
  python scripts/smoke_enroll.py https://<ngrok> # hits a remote base URL
"""

import io
import sys
import time

import httpx
import numpy as np
from PIL import Image

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"
CROP_PX = 256  # > 224 min; random noise => high Laplacian variance (passes sharpness)


def make_crop() -> bytes:
    """A 256x256 mid-brightness noise JPEG: passes dimension/sharpness/exposure gates."""
    arr = np.random.randint(60, 200, (CROP_PX, CROP_PX, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main() -> int:
    with httpx.Client(timeout=60) as c:
        print(f"[1] POST {BASE}/registration/start (auth-less)")
        r = c.post(f"{BASE}/registration/start", json={})
        r.raise_for_status()
        reg = r.json()
        job_id = reg["embedding_job_id"]
        print(f"    registration_id={reg['registration_id']}")
        print(f"    embedding_job_id={job_id}")
        print(f"    presigned_urls={len(reg['presigned_urls'])}")

        print("[2] PUT 8 crops directly to Supabase Storage")
        for u in reg["presigned_urls"]:
            blob = make_crop()
            up = c.put(u["url"], content=blob, headers={"Content-Type": "image/jpeg"})
            if up.status_code not in (200, 201):
                print(f"    crop {u['index']} upload FAILED: {up.status_code} {up.text[:200]}")
                return 1
        print("    all 8 uploaded")

        print(f"[3] POST {BASE}/inference/start")
        r = c.post(f"{BASE}/inference/start", json={"embedding_job_id": job_id})
        r.raise_for_status()
        print(f"    {r.json()}")

        print("[4] poll /inference/result until terminal")
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            r = c.get(f"{BASE}/inference/result/{job_id}")
            r.raise_for_status()
            res = r.json()
            if res.get("status") != last:
                last = res.get("status")
                print(f"    status={last}")
            if res.get("status") in ("complete", "failed"):
                print("    final:", res)
                if res.get("status") == "complete":
                    print(f"\nRESULT: complete  dog_id={res.get('dog_id')} "
                          f"duplicate_found={res.get('duplicate_found')} "
                          f"version={res.get('embedding_version')}")
                    return 0
                print("\nRESULT: failed —", res.get("quality_notes"))
                return 1
            time.sleep(2)
        print("\nRESULT: timed out waiting for terminal status")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
