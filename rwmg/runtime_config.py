"""Host-injected knobs for ``Engine`` / ``ResonanceWeightedMemoryGraph``.

The core never reads YAML or environment simulation files; the embedding app
must build a ``RuntimeConfig`` (or delegate to adapters in the harness layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class RuntimeConfig:
    agent_id: str = "default"
    root_dir: Path = Path(".rwmg_memory")
    max_memories: int = 3
    memory_token_cap: int = 120
    retention_factor: float = 0.92
    learning_rate: float = 0.35
    min_weight: float = -1.0
    max_weight: float = 1.0
    threshold: float = 0.05
    epsilon: float = 0.2
    gamma: float = 0.5
    temporal_window: int = 4
    deterministic_seed: int = 0
    production_mode: bool = False
    deterministic_clock: bool = False
    max_memory_entries: int = 5000
    trace_git_revision: Optional[str] = None
    trace_config_fingerprint: str = ""
    shadow_mode: bool = False
    #: In ``production_mode``, allow bounded epsilon when last trace confidence is below threshold.
    gated_exploration: bool = False
    gated_exploration_epsilon: float = 0.08
    gated_exploration_confidence_threshold: float = 0.45
    circuit_failure_threshold: int = 5
    circuit_cooldown_episodes: int = 8
    #: Generic simulation metadata (e.g. archetype multipliers) for host adapters — ignored by core.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def graph_kwargs(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "root_dir": self.root_dir,
            "max_memories": self.max_memories,
            "memory_token_cap": self.memory_token_cap,
            "retention_factor": self.retention_factor,
            "learning_rate": self.learning_rate,
            "min_w": self.min_weight,
            "max_w": self.max_weight,
            "threshold": self.threshold,
            "epsilon": self.epsilon,
            "gamma": self.gamma,
            "temporal_window": self.temporal_window,
            "deterministic_seed": self.deterministic_seed,
            "production_mode": self.production_mode,
            "deterministic_clock": self.deterministic_clock,
            "max_memory_entries": self.max_memory_entries,
            "trace_git_revision": self.trace_git_revision,
            "trace_config_fingerprint": self.trace_config_fingerprint,
            "shadow_mode": self.shadow_mode,
            "gated_exploration": self.gated_exploration,
            "gated_exploration_epsilon": self.gated_exploration_epsilon,
            "gated_exploration_confidence_threshold": self.gated_exploration_confidence_threshold,
            "circuit_failure_threshold": self.circuit_failure_threshold,
            "circuit_cooldown_episodes": self.circuit_cooldown_episodes,
        }


__all__ = ["RuntimeConfig"]
