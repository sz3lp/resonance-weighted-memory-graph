"""Stable public façade over the resonance-weighted memory graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from rwmg.memory_loop import ResonanceWeightedMemoryGraph
from rwmg.model_provider import HeuristicModelProvider, ModelProvider
from rwmg.runtime_config import RuntimeConfig
from rwmg.runtime_state import RuntimeState
from rwmg.storage import FileStorageBackend, StorageBackend

log = logging.getLogger("rwmg.engine")


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of ``Engine.process`` / ``aprocess`` — single external decision API."""

    output_text: str
    runtime_state: RuntimeState

    @property
    def policy_state(self) -> Dict[str, Any]:
        return self.runtime_state.policy_state

    @property
    def trace(self) -> Optional[Dict[str, Any]]:
        """Minimal execution trace (hash-centric); prefer :attr:`runtime_state`."""

        return self.runtime_state.execution_trace.as_dict()

    def __str__(self) -> str:
        return self.output_text


class Engine:
    """Host-facing entry: one coherent decision runtime (see ``ResonanceWeightedMemoryGraph``)."""

    def __init__(
        self,
        *,
        agent_id: str,
        config: RuntimeConfig,
        backend: StorageBackend,
        model: Optional[ModelProvider] = None,
    ):
        self.agent_id = agent_id
        self.config = replace(config, agent_id=agent_id)
        self.backend = backend
        self.model: ModelProvider = model or HeuristicModelProvider()

        kw = self.config.graph_kwargs()
        kw["storage_backend"] = backend
        kw["model_provider"] = self.model
        self._graph = ResonanceWeightedMemoryGraph(**kw)

    @classmethod
    def with_file_backend(
        cls,
        *,
        agent_id: str,
        config: RuntimeConfig,
        root_dir: Optional[Path] = None,
        model: Optional[ModelProvider] = None,
    ) -> Engine:
        rp = Path(root_dir or config.root_dir)
        backend = FileStorageBackend(rp, agent_id)
        cfg = replace(config, root_dir=rp, agent_id=agent_id)
        return cls(agent_id=agent_id, config=cfg, backend=backend, model=model)

    def process(self, input_string: str) -> ProcessResult:
        out = self._graph.process(input_string)
        rs = self._graph.last_runtime_state
        if rs is None:
            raise RuntimeError("internal graph failed to attach runtime state")
        return ProcessResult(output_text=out, runtime_state=rs)

    async def aprocess(self, input_string: str) -> ProcessResult:
        out = await self._graph.aprocess(input_string)
        rs = self._graph.last_runtime_state
        if rs is None:
            raise RuntimeError("internal graph failed to attach runtime state")
        return ProcessResult(output_text=out, runtime_state=rs)

    def feedback(self, event_id: str, signal: float) -> Tuple[float, float]:
        return self._graph.feedback(event_id, signal)


__all__ = ["Engine", "ProcessResult"]
