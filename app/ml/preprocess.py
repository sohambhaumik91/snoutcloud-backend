"""Image preprocessing + per-crop quality gating.

Mirrors the inference-time preprocessing used during training
(see D:/biometrics_v2/dataset.py — `get_transforms()` val branch + CLAHETransform).
The checkpoint `best_supcon_clahe_gem.pt` was trained with CLAHE on, so we apply
it here too. Skipping CLAHE at inference would shift the input distribution and
degrade retrieval accuracy.
"""

import io
import logging

import cv2
import numpy as np
import torch
import torchvision.transforms as T
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

IMAGE_SIZE = 224
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# Quality thresholds — sourced from settings so they can be tuned via .env
# (QUALITY_MIN_DIM, QUALITY_MIN_SHARPNESS, QUALITY_MIN_BRIGHTNESS,
# QUALITY_MAX_BRIGHTNESS) without a code change.
MIN_DIM = settings.quality_min_dim
MIN_LAPLACIAN_VAR = settings.quality_min_sharpness   # variance-of-Laplacian floor
MIN_BRIGHTNESS = settings.quality_min_brightness     # mean luminance, 0-255
MAX_BRIGHTNESS = settings.quality_max_brightness

_val_transform = T.Compose([
    T.Resize((IMAGE_SIZE, IMAGE_SIZE)),
    T.ToTensor(),
    T.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
])

# cv2.createCLAHE returns a stateful object but the `apply()` call itself
# is thread-safe for read-only use across crops.
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def _apply_clahe(img: Image.Image) -> Image.Image:
    """LAB-space CLAHE on L channel only (training-time recipe)."""
    arr = np.array(img)                              # PIL RGB → np uint8
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    lab = cv2.merge([l, a, b])
    bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def preprocess_for_inference(img_bytes: bytes) -> torch.Tensor:
    """bytes → CLAHE → resize 224 → ToTensor → ImageNet normalize. Returns (3,224,224)."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img = _apply_clahe(img)
    return _val_transform(img)


def quality_check(img_bytes: bytes) -> tuple[bool, dict]:
    """Per-crop quality gate. Returns (passed, notes_dict).

    Notes are persisted to embedding_jobs.quality_notes so the client can show
    the user which crops failed and why.
    """
    notes: dict = {}
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        return False, {"passed": False, "reason": "decode_failed", "error": str(e)}

    w, h = img.size
    notes["width"] = w
    notes["height"] = h
    if w < MIN_DIM or h < MIN_DIM:
        notes["passed"] = False
        notes["reason"] = "below_min_dim"
        return False, notes

    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    notes["sharpness"] = lap_var
    if lap_var < MIN_LAPLACIAN_VAR:
        notes["passed"] = False
        notes["reason"] = "too_blurry"
        return False, notes

    brightness = float(gray.mean())
    notes["brightness"] = brightness
    if brightness < MIN_BRIGHTNESS or brightness > MAX_BRIGHTNESS:
        notes["passed"] = False
        notes["reason"] = "bad_exposure"
        return False, notes

    notes["passed"] = True
    return True, notes
