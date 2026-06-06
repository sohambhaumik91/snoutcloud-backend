"""Lazy-loading wrapper around the NoseEncoder checkpoint.

The 380MB checkpoint is loaded once on first call (or by lifespan warmup in
main.py) and reused for every request. Forward passes run in a torch.no_grad
context on whatever device is available (CUDA if present, else CPU).
"""

import logging
import threading
from pathlib import Path

import torch

from app.core.config import settings
from app.ml.model import NoseEncoder

logger = logging.getLogger(__name__)

_encoder: NoseEncoder | None = None
_device: torch.device | None = None
_lock = threading.Lock()


def _checkpoint_path() -> Path:
    """Resolve the local checkpoint path, or fail with a clear message.

    The ~380MB checkpoint is baked into the Docker image at build time (it's too
    large for git and exceeds Supabase Storage's 50MB upload cap, so it is neither
    committed nor downloaded). The worker loads it from `settings.nose_model_path`.
    """
    path = Path(settings.nose_model_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Nose checkpoint not found at {path}. It is baked into the Docker "
            f"image (place it at models/best_supcon_clahe_gem.pt before building; "
            f"the worker reads NOSE_MODEL_PATH=/app/models/best_supcon_clahe_gem.pt). "
            f"For local dev, set NOSE_MODEL_PATH to your local copy."
        )
    return path.resolve()


def _load() -> tuple[NoseEncoder, torch.device]:
    global _encoder, _device

    ckpt_path = _checkpoint_path()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("loading nose encoder from %s onto %s", ckpt_path, device)

    # weights_only=False — checkpoint is a dict {model, optimizer, scheduler, cfg}
    # produced by torch.save(). Trusted source (our own training repo).
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    cfg = ckpt["cfg"]

    model = NoseEncoder(
        embed_dim=cfg["embed_dim"],
        proj_dim=cfg["proj_dim"],
        n_heads=cfg["n_heads"],
        n_layers=cfg["n_layers"],
        pool=cfg.get("pool", "cls"),
        pretrained=False,  # checkpoint state_dict carries the trained backbone
    )
    model.load_state_dict(ckpt["model"])
    model.to(device).eval()

    _encoder = model
    _device = device
    logger.info("nose encoder ready (pool=%s, embed_dim=%d)", cfg.get("pool"), cfg["embed_dim"])
    return _encoder, _device


def get_encoder() -> tuple[NoseEncoder, torch.device]:
    """Return (model, device), loading the checkpoint lazily on first call."""
    global _encoder, _device
    if _encoder is None:
        with _lock:
            if _encoder is None:
                return _load()
    return _encoder, _device  # type: ignore[return-value]


def warmup() -> None:
    """Call from lifespan startup to pay the load cost up front, not on first request."""
    get_encoder()


@torch.no_grad()
def embed_batch(tensor_batch: torch.Tensor) -> torch.Tensor:
    """Run model.embed() on a (N, 3, 224, 224) batch. Returns (N, embed_dim) on CPU."""
    model, device = get_encoder()
    out = model.embed(tensor_batch.to(device))
    return out.detach().cpu()
