"""Fleet-wide concurrency limits for async model providers."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rwmg.engine import Engine, ProcessResult
from rwmg.model_provider import ModelProvider


class ThrottledModelProvider(ModelProvider):
    """Wraps a :class:`ModelProvider` and serializes async calls behind a semaphore."""

    def __init__(self, inner: ModelProvider, sem: asyncio.Semaphore) -> None:
        self._inner = inner
        self._sem = sem

    def predict_expected_score(
        self,
        input_text: str,
        candidate_memories: Sequence[Dict[str, Any]],
        *,
        model_variant: str = "baseline",
    ) -> float:
        return self._inner.predict_expected_score(
            input_text, candidate_memories, model_variant=model_variant
        )

    def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self._inner.evaluate(input_text, output_text, context)

    async def apredict_expected_score(
        self,
        input_text: str,
        candidate_memories: Sequence[Dict[str, Any]],
        *,
        model_variant: str = "baseline",
    ) -> float:
        async with self._sem:
            return await self._inner.apredict_expected_score(
                input_text, candidate_memories, model_variant=model_variant
            )

    async def aevaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        async with self._sem:
            return await self._inner.aevaluate(input_text, output_text, context)


class FleetManager:
    """Coordinates many :class:`Engine` instances with a shared model-call throttle."""

    def __init__(self, max_concurrent_model_calls: int = 4) -> None:
        self.max_concurrent_model_calls = max_concurrent_model_calls
        self._sem = asyncio.Semaphore(max_concurrent_model_calls)

    def throttle(self, model: ModelProvider) -> ModelProvider:
        return ThrottledModelProvider(model, self._sem)

    async def aprocess_all(self, jobs: Sequence[Tuple[Engine, str]]) -> List[ProcessResult]:
        return list(await asyncio.gather(*(eng.aprocess(text) for eng, text in jobs)))


__all__ = ["FleetManager", "ThrottledModelProvider"]
