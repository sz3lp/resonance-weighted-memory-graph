"""Phase 11 — determinism, replay, and collapse invariants (single execution graph)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rwmg import Engine, RuntimeConfig
from dataclasses import fields

from rwmg.memory_loop import validate_schema
from rwmg.runtime_state import ExecutionTrace, derive_global_seed, policy_state_fingerprint
from rwmg.utils.replay import verify_replay


def _cfg(**kwargs):
    defaults = {
        "production_mode": True,
        "epsilon": 0.0,
        "deterministic_seed": 99,
        "deterministic_clock": True,
        "trace_config_fingerprint": "fp_phase11",
    }
    merged = {**defaults, **kwargs}
    return RuntimeConfig(**merged)


class TestDeterminism:
    """Identical roots + input → identical output and trace hashes (single-machine gate)."""

    def test_identical_runs_match(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        cfg = _cfg()
        e1 = Engine.with_file_backend(agent_id="ag", config=cfg, root_dir=d1)
        e2 = Engine.with_file_backend(agent_id="ag", config=cfg, root_dir=d2)
        inp = "deterministic structured policy verify"
        r1 = e1.process(inp)
        r2 = e2.process(inp)
        assert r1.output_text == r2.output_text
        a = r1.runtime_state.execution_trace.as_dict()
        b = r2.runtime_state.execution_trace.as_dict()
        a.pop("runtime_ms", None)
        b.pop("runtime_ms", None)
        assert a == b
        assert r1.runtime_state.global_seed == r2.runtime_state.global_seed == derive_global_seed(
            "fp_phase11", "ag"
        )

    def test_global_seed_single_source(self) -> None:
        g = derive_global_seed("abc", "x")
        h = derive_global_seed("abc", "x")
        assert g == h
        assert derive_global_seed("abc", "y") != g


class TestReplay:
    """Frozen snapshot + process → reproducible minimal trace fields."""

    def test_replay_output_and_optional_memory_hash(self, tmp_path: Path) -> None:
        cfg = _cfg(max_memories=2, deterministic_clock=False)
        eng = Engine.with_file_backend(agent_id="agent", config=cfg, root_dir=tmp_path)
        before = json.loads(json.dumps(eng._graph.store.load_store()))
        inp = "replay hash consistency check"
        r = eng.process(inp)
        validate_schema(eng._graph.store.load_store())
        trace_pkg = {
            "input": inp,
            "output": r.output_text,
            "agent_id": "agent",
            "initial_memory_store": before,
            "policy_state_snapshot": r.policy_state,
            "config_hash": "fp_phase11",
        }
        scratch = tmp_path / "scratch"
        verify_replay(trace_pkg, scratch_root=scratch, agent_id="agent", fingerprint="fp_phase11")


class TestCollapse:
    """Engine.process remains the only façade; graph internals stay reachable only for tests."""

    def test_engine_exposes_runtime_state_only(self, tmp_path: Path) -> None:
        eng = Engine.with_file_backend(agent_id="a1", config=_cfg(), root_dir=tmp_path)
        r = eng.process("hello coherence")
        assert isinstance(r.runtime_state.execution_trace, ExecutionTrace)
        assert r.trace == r.runtime_state.execution_trace.as_dict()
        assert len(r.trace) == len(fields(ExecutionTrace))
        hist = eng._graph.inspect_traces()
        assert hist
        assert set(hist[-1].keys()) == {f.name for f in fields(ExecutionTrace)}

    def test_async_engine_process_result_shape(self, tmp_path: Path) -> None:
        eng = Engine.with_file_backend(agent_id="a2", config=_cfg(), root_dir=tmp_path / "asy")

        async def _once():
            return await eng.aprocess("async pathway")

        r = asyncio.run(_once())
        assert r.runtime_state.execution_trace.runtime_ms >= 0.0
        assert len(policy_state_fingerprint(r.policy_state)) == 32
