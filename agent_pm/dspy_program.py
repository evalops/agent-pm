"""DSPy module for compiling planner guidance."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

try:
    import dspy  # type: ignore[attr-defined]
except ImportError as exc:  # pragma: no cover - optional dependency
    dspy: Any | None = None
    _dspy_import_error: ImportError | None = exc
else:  # pragma: no cover - exercised in environments with DSPy installed
    _dspy_import_error = None

from .settings import settings

logger = logging.getLogger(__name__)


if dspy is not None:  # pragma: no branch - class definitions depend on import success
    class PlannerSignature(dspy.Signature):
        title = dspy.InputField(desc="Idea title")
        context = dspy.InputField(desc="Idea context")
        constraints = dspy.InputField(desc="Constraints (comma separated)")
        structured_brief = dspy.OutputField(desc="Optimized planning guidance")


    class PlannerProgram(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.generate = dspy.Predict(PlannerSignature)

        def forward(self, title: str, context: str, constraints: str) -> str:
            result = self.generate(title=title, context=context, constraints=constraints)
            return result.structured_brief
else:
    PlannerSignature = None
    PlannerProgram = None


@lru_cache(maxsize=1)
def _configured_program() -> PlannerProgram:
    api_key = settings.openai_api_key
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for DSPy planner")

    if dspy is None or PlannerProgram is None:
        raise RuntimeError(
            "DSPy is not installed. Install the 'dspy-ai' package to enable planner compilation."
        ) from _dspy_import_error

    dspy.settings.configure(
        lm=dspy.OpenAI(model="gpt-4.1-mini", api_key=api_key),
    )
    return PlannerProgram()


def compile_brief(title: str, context: str, constraints: list[str]) -> str:
    if not settings.openai_api_key:
        if settings.dry_run:
            logger.info("DSPy compile skipped in dry-run mode")
            return ""
        raise RuntimeError("OPENAI_API_KEY is required for DSPy planner")

    if dspy is None or PlannerProgram is None:
        if settings.dry_run:
            logger.info("DSPy not installed; returning empty brief in dry-run mode")
            return ""
        raise RuntimeError(
            "DSPy is not installed. Install the 'dspy-ai' package to enable planner compilation."
        ) from _dspy_import_error

    program = _configured_program()
    combined = ", ".join(constraints)
    try:
        return program(title=title, context=context, constraints=combined)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("DSPy compile failed: %s", exc)
        return ""


__all__ = ["compile_brief"]
