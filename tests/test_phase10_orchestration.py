"""Phase 10: async engine parity, fleet throughput, storage locks, Redis, telemetry."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from rwmg import Engine, FleetManager, RuntimeConfig, TelemetryProbe
from rwmg.memory_loop import SCHEMA_VERSION, MODEL_VARIANTS, MemoryStore
from rwmg.model_provider import HeuristicModelProvider
from rwmg.storage import (
    FileStorageBackend,
    InMemoryStorageBackend,
    RedisStorageBackend,
    StorageRevisionConflict,
)


def _det_cfg() -> RuntimeConfig:
    return RuntimeConfig(
        production_mode=True,
        epsilon=0.0,
        deterministic_seed=42,
        deterministic_clock=True,
        max_memories=2,
    )


def _minimal_valid_store(agent_id: str = "agent_x") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "agent_id": agent_id,
        "storage_revision": 0,
        "events": {},
        "event_log": [],
        "feedback_log": [],
        "process_traces": [],
        "clusters": {},
        "policy_state": {
            "preferred_patterns": {},
            "suppressed_patterns": {},
            "exploration_rate": 0.0,
            "evaluation_trend": [],
            "cluster_performance": {},
            "prediction_error_moving_avg": 0.0,
        },
        "self_model": {
            "prediction_bias": 0.0,
            "regret_bias": 0.0,
            "causal_attribution_bias": 0.0,
            "calibration_error": 0.0,
            "confidence_drift": 0.0,
            "global_calibration_error": 0.0,
            "model_rankings": {v: 0.5 for v in MODEL_VARIANTS},
            "drift_trend": 0.0,
            "meta_score_history": [],
            "prediction_error_history": [],
            "regret_error_history": [],
            "attribution_error_history": [],
            "confidence_drift_history": [],
        },
    }


def test_async_engine_parity_with_sync(tmp_path: Path) -> None:
    async def _run() -> None:
        d1 = tmp_path / "sync"
        d2 = tmp_path / "async"
        cfg = _det_cfg()
        es = Engine.with_file_backend(agent_id="agent", config=cfg, root_dir=d1)
        ea = Engine.with_file_backend(agent_id="agent", config=cfg, root_dir=d2)
        text = "solar policy structured answer verify outcome"
        rs = es.process(text)
        ra = await ea.aprocess(text)
        assert ra.output_text == rs.output_text
        assert abs(
            float(ra.policy_state["prediction_error_moving_avg"])
            - float(rs.policy_state["prediction_error_moving_avg"])
        ) < 1e-9
        assert rs.runtime_state.execution_trace.input_hash == ra.runtime_state.execution_trace.input_hash
        assert rs.runtime_state.memory_store_hash == ra.runtime_state.memory_store_hash

    asyncio.run(_run())


def test_storage_lock_second_acquire_fails_while_held() -> None:
    backend = InMemoryStorageBackend()
    assert backend.lock(timeout=1.0) is True
    out: list[bool] = []

    def second() -> None:
        out.append(backend.lock(timeout=0.15))

    t = threading.Thread(target=second)
    t.start()
    t.join()
    assert out == [False]
    backend.unlock()
    assert backend.lock(timeout=0.5) is True
    backend.unlock()


def test_first_writer_wins_revision_conflict(tmp_path: Path) -> None:
    root = tmp_path / "fw"
    backend = FileStorageBackend(root, "agent")
    MemoryStore(backend, "agent")
    disk = backend.load()
    assert disk is not None
    assert int(disk["storage_revision"]) >= 1
    stale = dict(disk)
    stale["storage_revision"] = 0
    with pytest.raises(StorageRevisionConflict):
        backend.save(stale)


def test_fleet_ten_agents_aprocess_all(tmp_path: Path) -> None:
    fleet = FleetManager(max_concurrent_model_calls=2)
    cfg = _det_cfg()
    jobs = []
    for i in range(10):
        model = fleet.throttle(HeuristicModelProvider())
        eng = Engine.with_file_backend(
            agent_id=f"agent_{i}",
            config=cfg,
            root_dir=tmp_path / f"fleet_{i}",
            model=model,
        )
        jobs.append((eng, f"task {i} solar panel policy structured"))
    results = asyncio.run(fleet.aprocess_all(jobs))
    assert len(results) == 10
    assert all(r.output_text for r in results)


def test_telemetry_prediction_error_matches_policy_state(tmp_path: Path) -> None:
    eng = Engine.with_file_backend(
        agent_id="agent",
        config=_det_cfg(),
        root_dir=tmp_path / "telemetry",
    )
    t0 = time.perf_counter()
    r = eng.process("alpha policy solar verify")
    dt = time.perf_counter() - t0
    probe = TelemetryProbe()
    probe.observe_cycle(trace=r.trace, policy_state=r.policy_state, latency_s=dt)
    exported = probe.to_json()["prediction_error"]
    internal = float(r.policy_state["prediction_error_moving_avg"])
    assert abs(exported - internal) < 1e-4
    prom = probe.to_prometheus()
    assert "rwmg_prediction_error_moving_avg" in prom
    assert "rwmg_latency_p99_seconds" in prom


def test_redis_storage_roundtrip() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.FakeRedis()
    backend = RedisStorageBackend(r, "agent_x")
    store = _minimal_valid_store("agent_x")
    backend.save(store)
    loaded = backend.load()
    assert loaded is not None
    assert int(loaded["storage_revision"]) >= 1
    loaded["events"]["e1"] = {
        "id": "e1",
        "agent_id": "agent_x",
        "input": "hello",
        "output": "Answer: hello world",
        "outcome_signal": 0.0,
        "weight": 0.5,
        "timestamp": 1,
        "type": "interaction",
        "embedding": {},
        "future_score": 0.0,
        "usage_count": 0,
        "cluster_id": "",
        "expected_value": 0.5,
        "variance": 0.0,
        "recent_scores": [],
        "marginal_effect": 0.0,
        "sensitivity_score": 0.0,
        "counterfactual_deltas": [],
        "avg_counterfactual_delta": 0.0,
    }
    backend.save(loaded)
    final = backend.load()
    assert final is not None
    assert final["events"]["e1"]["input"] == "hello"
