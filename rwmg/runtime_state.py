"""Canonical runtime state for Phase 11 — single source of truth per decision cycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional


def derive_global_seed(config_fingerprint: str, agent_id: str) -> int:
    """Single system seed: ``sha256(config_hash || agent_id) → int`` (no episode-level seeds)."""

    blob = f"{config_fingerprint}:{agent_id}".encode("utf-8")
    return int(hashlib.sha256(blob).hexdigest()[:16], 16)


def policy_state_fingerprint(policy: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True)
class ExecutionTrace:
    """Minimal append-only audit record (hashes only + bounded identifiers)."""

    input_hash: str
    output_hash: str
    memory_state_hash_before: str
    memory_state_hash_after: str
    policy_state_hash: str
    selected_action_id: str
    runtime_ms: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeState:
    """Canonical post-step view; no duplicated policy payloads beyond store/dict here."""

    memory_store_hash: str
    policy_state: Dict[str, Any]
    config_hash: str
    last_output_hash: str
    global_seed: int
    execution_trace: ExecutionTrace
    output_text: str = ""

    def frozen_execution_dict(self) -> Dict[str, Any]:
        """Immutable snapshot for persistence (append-only)."""

        return {
            **self.execution_trace.as_dict(),
            "config_hash": self.config_hash,
            "global_seed": self.global_seed,
            "memory_store_hash": self.memory_store_hash,
            "last_output_hash": self.last_output_hash,
        }


__all__ = [
    "ExecutionTrace",
    "RuntimeState",
    "derive_global_seed",
    "policy_state_fingerprint",
]
