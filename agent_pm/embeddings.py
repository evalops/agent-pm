"""Embeddings and semantic search using OpenAI API (no heavy ML libraries)."""

from __future__ import annotations

import logging

from agent_pm.openai_utils import get_async_openai_client

logger = logging.getLogger(__name__)


async def generate_embedding(text: str, model: str = "text-embedding-3-small") -> list[float] | None:
    """Generate an embedding vector using the OpenAI API.

    Returns ``None`` when no OpenAI client is available (dry-run mode) so
    callers can skip embedding-dependent features. Raises on API failure.
    Embeddings are never fabricated.
    """
    client = get_async_openai_client()
    if client is None:
        logger.info("No OpenAI client available (dry-run mode); skipping embedding generation")
        return None
    try:
        response = await client.embeddings.create(input=text, model=model)
        return response.data[0].embedding
    except Exception as exc:
        logger.error("Failed to generate embedding: %s", exc)
        raise


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import math

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(y * y for y in b))
    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0
    return dot_product / (magnitude_a * magnitude_b)


async def search_similar_plans(
    query_embedding: list[float],
    candidate_embeddings: list[tuple[str, list[float]]],
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Find top-k most similar plans by cosine similarity.

    Args:
        query_embedding: Query vector
        candidate_embeddings: List of (plan_id, embedding) tuples
        top_k: Number of results to return

    Returns:
        List of (plan_id, similarity_score) tuples, sorted by score descending
    """
    similarities = [(plan_id, cosine_similarity(query_embedding, emb)) for plan_id, emb in candidate_embeddings]
    similarities.sort(key=lambda x: x[1], reverse=True)
    return similarities[:top_k]
