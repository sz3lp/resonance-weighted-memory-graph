"""Model-agnostic prediction and evaluation hooks (LLM or heuristics)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, Sequence


class ModelProvider(ABC):
    """Hosts must implement prediction and scoring used by the memory policy."""

    @abstractmethod
    def predict_expected_score(
        self,
        input_text: str,
        candidate_memories: Sequence[Dict],
        *,
        model_variant: str = "baseline",
    ) -> float:
        """Expected usefulness of activating the given retrieval set."""

    @abstractmethod
    def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict],
    ) -> Dict:
        """Return ``{\"score\",\"components\",\"confidence\"}``-style evaluation."""

    async def apredict_expected_score(
        self,
        input_text: str,
        candidate_memories: Sequence[Dict],
        *,
        model_variant: str = "baseline",
    ) -> float:
        """Async variant for I/O-bound models; default wraps the sync implementation."""

        return self.predict_expected_score(
            input_text, candidate_memories, model_variant=model_variant
        )

    async def aevaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict],
    ) -> Dict:
        """Async evaluation hook; default wraps :meth:`evaluate`."""

        return self.evaluate(input_text, output_text, context)


class HeuristicModelProvider(ModelProvider):
    """Default deterministic TF-IDF + rule-based scorer (offline, no external APIs)."""

    def predict_expected_score(
        self,
        input_text: str,
        candidate_memories: Sequence[Dict],
        *,
        model_variant: str = "baseline",
    ) -> float:
        # Late import: ``memory_loop`` wires ``ModelProvider`` into policy_context.
        from rwmg.memory_loop import heuristic_predict_expected_score

        return heuristic_predict_expected_score(
            input_text, list(candidate_memories), model_variant=model_variant
        )

    def evaluate(
        self,
        input_text: str,
        output_text: str,
        context: Optional[Dict],
    ) -> Dict:
        from rwmg.memory_loop import heuristic_evaluate_output

        return heuristic_evaluate_output(input_text, output_text, context)


__all__ = ["HeuristicModelProvider", "ModelProvider"]
