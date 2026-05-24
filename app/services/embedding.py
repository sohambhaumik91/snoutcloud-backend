import asyncio
from functools import lru_cache
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load model once and cache. ~90MB weights, runs on CPU."""
    return SentenceTransformer(MODEL_NAME)


async def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns 384-dim vector."""
    model = get_model()
    embedding = await asyncio.to_thread(
        model.encode, text.strip(), normalize_embeddings=True
    )
    return embedding.tolist()


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple strings in one call."""
    if not texts:
        return []
    model = get_model()
    embeddings = await asyncio.to_thread(
        model.encode, [t.strip() for t in texts], normalize_embeddings=True
    )
    return [e.tolist() for e in embeddings]
