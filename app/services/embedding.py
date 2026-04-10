from openai import AsyncOpenAI
from app.core.config import settings

_client: AsyncOpenAI | None = None


def get_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns 1536-dim vector."""
    client = get_openai()
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text.strip(),
        dimensions=settings.embedding_dimensions
    )
    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple strings in one API call."""
    if not texts:
        return []
    client = get_openai()
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=[t.strip() for t in texts],
        dimensions=settings.embedding_dimensions
    )
    return [item.embedding for item in response.data]
