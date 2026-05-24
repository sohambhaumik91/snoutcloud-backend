"""Nose-crop embedding pipeline.

Phase 1 stub: the real pipeline (quality gating -> ConvNeXt+ArcFace inference ->
duplicate check -> persistence) will be implemented later. For now this is a
placeholder so /inference/start can spawn it as a BackgroundTask end-to-end.
"""

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


async def run_pipeline(embedding_job_id: UUID) -> None:
    """Empty stub. Logs that the pipeline was kicked off."""
    logger.info("pipeline started for %s", embedding_job_id)
