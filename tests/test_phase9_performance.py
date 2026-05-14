"""Phase 9: vectorized retrieval parity, shadow mode, serialization, and latency."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from rwmg import Engine, RuntimeConfig
from rwmg.memory_loop import (
    SCHEMA_VERSION,
    MODEL_VARIANTS,
    MemoryEvent,
    RetrievalEngine,
    _migrate_legacy_memory_store,
    embed,
    validate_schema,
)
from rwmg.storage import FileStorageBackend, MsgPackStorageBackend


def _minimal_event(eid: str, text: str, weight: float, ts: int) -> dict:
    e = embed(text)
    pad = "x" * 120
    return {
        "id": eid,
        "agent_id": "agent",
        "input": text,
        "output": f"Answer: body {eid} {pad}",
        "outcome_signal": 0.0,
        "weight": weight,
        "timestamp": ts,
        "type": "interaction",
        "embedding": e,
        "future_score": 0.0,
        "usage_count": 0,
        "cluster_id": "cluster_1",
        "expected_value": 0.5,
        "variance": 0.0,
        "recent_scores": [],
        "marginal_effect": 0.0,
        "sensitivity_score": 0.0,
        "counterfactual_deltas": [],
        "avg_counterfactual_delta": 0.0,
    }


def _empty_store(agent_id: str = "agent") -> dict:
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
            "exploration_rate": 0.2,
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


def test_vectorized_retrieval_matches_reference_ranking():
    rng = RetrievalEngine(max_memories=3, threshold=0.05)
    clusters = {
        "cluster_1": {"shared_weight": 0.5, "expected_value": 0.5, "usage_count": 0},
    }
    events = [
        MemoryEvent(
            id="a",
            agent_id="agent",
            input="alpha policy solar",
            output="Answer: long alpha",
            outcome_signal=0.0,
            weight=0.9,
            timestamp=1,
            embedding=embed("alpha policy solar"),
            future_score=0.5,
            usage_count=0,
            cluster_id="cluster_1",
            expected_value=0.6,
            variance=0.0,
            recent_scores=[],
            marginal_effect=0.0,
            sensitivity_score=0.0,
            counterfactual_deltas=[],
            avg_counterfactual_delta=0.0,
        ),
        MemoryEvent(
            id="b",
            agent_id="agent",
            input="beta solar panel",
            output="Answer: beta",
            outcome_signal=0.0,
            weight=0.7,
            timestamp=2,
            embedding=embed("beta solar panel"),
            future_score=0.4,
            usage_count=1,
            cluster_id="cluster_1",
            expected_value=0.5,
            variance=0.0,
            recent_scores=[],
            marginal_effect=0.0,
            sensitivity_score=0.0,
            counterfactual_deltas=[],
            avg_counterfactual_delta=0.0,
        ),
    ]
    q = "solar alpha roadmap"
    ref = rng.retrieve_reference(q, events, clusters)
    vec = rng.retrieve(q, events, clusters, use_reference=False)
    assert [m.event.id for m in ref] == [m.event.id for m in vec]
    for a, b in zip(ref, vec):
        assert abs(a.similarity - b.similarity) < 1e-6
        assert abs(a.score - b.score) < 1e-6


def test_shadow_mode_preserves_file_bytes(tmp_path: Path):
    fb = FileStorageBackend(tmp_path, "agent")
    store = _empty_store()
    store["events"]["seed"] = _minimal_event("seed", "seed phrase", 0.8, 100)
    _migrate_legacy_memory_store(store)
    validate_schema(store)
    fb.save(store)
    digest_before = hashlib.sha256(fb.store_path.read_bytes()).hexdigest()

    cfg = RuntimeConfig(
        shadow_mode=True,
        deterministic_clock=True,
        deterministic_seed=3,
        production_mode=True,
    )
    eng = Engine(agent_id="agent", config=cfg, backend=fb)
    eng.process("candidate policy probe text")

    digest_after = hashlib.sha256(fb.store_path.read_bytes()).hexdigest()
    assert digest_before == digest_after


def test_msgpack_faster_than_json_on_large_store(tmp_path: Path):
    n = 1200
    store = _empty_store()
    base = "corpus token solar alpha"
    for i in range(n):
        store["events"][f"e{i}"] = _minimal_event(f"e{i}", f"{base} {i % 17}", 0.4 + (i % 10) * 0.01, i)
    _migrate_legacy_memory_store(store)
    validate_schema(store)

    jb = FileStorageBackend(tmp_path / "j", "agent")
    mb = MsgPackStorageBackend(tmp_path / "m", "agent")

    rounds = 30
    t0 = time.perf_counter()
    for _ in range(rounds):
        jb.save(store)
        _ = jb.load()
    json_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    for _ in range(rounds):
        mb.save(store)
        _ = mb.load()
    mp_time = time.perf_counter() - t1

    assert mp_time < 0.5 * json_time, f"msgpack {mp_time:.4f}s vs json {json_time:.4f}s"


@pytest.mark.skipif(
    os.environ.get("RWMG_BENCHMARK") != "1",
    reason="set RWMG_BENCHMARK=1 to enforce <10ms retrieval on 5k memories",
)
def test_retrieval_latency_5k_entries():
    """Hardware gate: vectorized retrieval over 5k memories must stay under 10ms mean."""

    n = 5000
    store = _empty_store()
    txt = "shared vocabulary token solar alpha policy"
    for i in range(n):
        store["events"][f"e{i}"] = _minimal_event(f"e{i}", f"{txt} variant {i % 23}", 0.55, i)
    _migrate_legacy_memory_store(store)
    validate_schema(store)
    events = [
        MemoryEvent(
            id=f"e{i}",
            agent_id="agent",
            input=f"{txt} variant {i % 23}",
            output=f"Answer: {i}",
            outcome_signal=0.0,
            weight=0.55,
            timestamp=i,
            embedding=store["events"][f"e{i}"]["embedding"],
            future_score=0.0,
            usage_count=0,
            cluster_id="cluster_1",
            expected_value=0.5,
            variance=0.0,
            recent_scores=[],
            marginal_effect=0.0,
            sensitivity_score=0.0,
            counterfactual_deltas=[],
            avg_counterfactual_delta=0.0,
        )
        for i in range(n)
    ]
    clusters = {"cluster_1": {"shared_weight": 0.5, "expected_value": 0.5, "usage_count": 0}}
    eng = RetrievalEngine(max_memories=3, threshold=0.05)
    q = "solar alpha policy guidance"
    t0 = time.perf_counter()
    for _ in range(5):
        eng.retrieve(q, events, clusters)
    elapsed = (time.perf_counter() - t0) / 5.0
    assert elapsed < 0.010, f"mean retrieve {elapsed*1000:.2f}ms >= 10ms"
