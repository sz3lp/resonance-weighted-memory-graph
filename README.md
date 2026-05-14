RWMG (Resonance-Weighted Memory Graph)
A multi-module agent simulation and orchestration framework built to solve state degradation, context collapse, and API brittleness in long-running LLM systems.

Built in Python. ~8,800 LOC across 60+ modules. Fully test-backed.

⚡ The Problem It Solves
Most LLM agent architectures fall apart over long horizons: they echo their own outputs, lose context, fail ungracefully during API timeouts, and are fundamentally non-deterministic, making debugging a nightmare.

RWMG is an execution-layer framework designed from first principles to fix this. It provides deterministic memory ranking, pluggable distributed storage, and aggressive circuit-breaker fault tolerance, allowing fleets of agents to maintain coherent, evolving state over multiple epochs without breaking.

🏗️ Core Architecture & Subsystems
The system is decoupled into isolated modules, ensuring that storage, memory logic, orchestration, and LLM integrations can be modified or swapped independently.

1. Storage Abstraction (rwmg/storage/)
Agent state and memory graphs are decoupled from the execution logic. The system supports hot-swappable backends with optimistic concurrency control:

MsgPack Backend: Compact binary serialization for high-speed local I/O.

Redis Backend: Distributed cache patterns for horizontal scaling and per-agent locking.

File-Based Backend: Human-readable JSON fallbacks for debugging and inspection.

2. Deterministic Memory Loop (rwmg/memory_loop.py & rwmg/feedback/)
To prevent the "echo chamber" effect common in LLMs, RWMG does not just append strings to a context window. Memory is treated as a weighted graph:

Resonance & Weight Attribution: Memories are scored based on their impact and relevance to the current state.

Diversity Penalties & Exponential Decay: Prevents recent or highly resonant memories from dominating the agent's context, forcing emergent behavior over time.

3. Orchestration & Fleet Management (rwmg/sim_runner/ & rwmg/orchestration/)
Handles the lifecycle of multiple agents operating simultaneously.

Epoch Runner: Manages simulation cycles, ensuring state transitions happen predictably.

Fleet Manager: Orchestrates multi-agent interactions, maintaining isolation between individual agent states (runtime_state.py) while allowing governed interaction.

4. Fault Tolerance & API Integration (rwmg/circuit_breaker.py & rwmg/utils/api_wrappers.py)
LLM APIs fail. RWMG assumes failure is the default state.

Implements aggressive circuit breaker patterns to gracefully degrade agent behavior rather than crashing the loop.

Built-in fallback heuristics and prompt-tuning pipelines (prompt_tuner.py) that adapt dynamically if the primary model provider times out or returns malformed schema.

🧪 Testing & Validation
Building LLM wrappers is easy; making them testable is hard. RWMG was built with a rigorous, deterministic testing philosophy to catch architectural regressions.

The test suite (/tests) isolates behavior across multiple phases:

Isolation Tests: (test_phase8_isolation.py) Validates that agent states never bleed across boundaries.

Performance Benchmarks: (test_phase9_performance.py)

Orchestration Checks: (test_phase10_orchestration.py)

Invariant Validation: (test_phase11_invariants.py) Ensures that the core rules of the memory graph and state transitions hold true under stress.

Trace Replay: (scripts/replay_trace.py) Allows for exact replay of API calls and memory ranking decisions to reliably debug divergence.

💻 Tech Stack
Language: Python 3.x

Storage: Redis, MsgPack, JSON

Testing: Pytest (deterministic harnesses)

Integration: Gemini API (Extensible via model_provider.py)

🚀 Usage
(Provide a brief, 3-4 line block of code here showing how easy it is to initialize the engine, set the storage backend, and run an epoch. Keep it highly abstracted.)

Python
from rwmg.engine import RWMGEngine
from rwmg.storage import RedisBackend
from rwmg.sim_runner import EpochRunner

# Initialize storage and engine
storage = RedisBackend(host='localhost', port=6379)
engine = RWMGEngine(storage_backend=storage)

# Run simulation epoch
runner = EpochRunner(engine)
runner.execute_epoch(steps=10)
📬 Contact / About the Author
Built by Luke Preble — Systems Engineer focused on AI-native development, complex backend architectures, and creating resilient execution layers.

lukepreble@outlook.com
