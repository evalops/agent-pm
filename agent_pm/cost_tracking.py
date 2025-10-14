"""OpenAI cost tracking and token usage monitoring."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Pricing as of 2024 (USD per 1M tokens)
# Source: https://openai.com/api/pricing/
PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "text-embedding-3-small": {"input": 0.02, "output": 0.00},
    "text-embedding-3-large": {"input": 0.13, "output": 0.00},
}


@dataclass
class TokenUsage:
    """Token usage for a single API call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for given token usage."""
    pricing = PRICING.get(model)
    if not pricing:
        logger.warning("Unknown model for cost calculation: %s", model)
        return 0.0

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def extract_usage_from_response(response: Any, model: str) -> TokenUsage | None:
    """Extract token usage from OpenAI response object."""
    try:
        usage = response.usage
        if not usage:
            return None

        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        cost = calculate_cost(model, input_tokens, output_tokens)

        return TokenUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
    except Exception as exc:
        logger.warning("Failed to extract usage from response: %s", exc)
        return None


def log_usage(usage: TokenUsage, operation: str, **extra):
    """Log token usage with cost information."""
    logger.info(
        "OpenAI usage for %s",
        operation,
        extra={
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "cost_usd": f"${usage.cost_usd:.6f}",
            **extra,
        },
    )
