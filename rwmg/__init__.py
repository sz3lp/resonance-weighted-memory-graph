"""RWMG: resonance-weighted memory graph (standalone policy engine).

Simulation harnesses (`sim_runner`, `lifecycle_manager`, etc.) live beside this
package but are **not** imported by the core runtime.
"""

from rwmg.engine import Engine, ProcessResult
from rwmg.runtime_state import ExecutionTrace, RuntimeState, derive_global_seed, policy_state_fingerprint
from rwmg.model_provider import HeuristicModelProvider, ModelProvider
from rwmg.orchestration import FleetManager, ThrottledModelProvider
from rwmg.runtime_config import RuntimeConfig
from rwmg.storage import (
    FileStorageBackend,
    InMemoryStorageBackend,
    MsgPackStorageBackend,
    RedisStorageBackend,
    StorageBackend,
    StorageRevisionConflict,
)
from rwmg.utils.telemetry import TelemetryProbe, TelemetrySnapshot

__all__ = [
    "Engine",
    "ExecutionTrace",
    "derive_global_seed",
    "FileStorageBackend",
    "FleetManager",
    "HeuristicModelProvider",
    "InMemoryStorageBackend",
    "MsgPackStorageBackend",
    "ModelProvider",
    "policy_state_fingerprint",
    "ProcessResult",
    "RedisStorageBackend",
    "RuntimeConfig",
    "RuntimeState",
    "StorageBackend",
    "StorageRevisionConflict",
    "TelemetryProbe",
    "TelemetrySnapshot",
    "ThrottledModelProvider",
]

__version__ = "0.8.0"
