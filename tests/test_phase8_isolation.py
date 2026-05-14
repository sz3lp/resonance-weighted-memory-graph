import json
from unittest.mock import MagicMock

from rwmg import Engine, InMemoryStorageBackend, ModelProvider, RuntimeConfig
from rwmg.memory_loop import MemoryEvent, ResonanceWeightedMemoryGraph, embed


def test_engine_memory_vs_file_backend_policy_match(tmp_path):
    cfg = RuntimeConfig(
        deterministic_clock=True,
        deterministic_seed=7,
        production_mode=False,
        epsilon=0.15,
    )
    mem_back = InMemoryStorageBackend()
    eng_mem = Engine(agent_id="agent", config=cfg, backend=mem_back)

    ef = Engine.with_file_backend(agent_id="agent", config=cfg, root_dir=tmp_path)

    payload = MemoryEvent(
        id="seed",
        agent_id="agent",
        input="seed task",
        output="Answer: seed body",
        outcome_signal=0.0,
        weight=0.8,
        timestamp=900_000,
        type="interaction",
        embedding=embed("seed task"),
        future_score=0.5,
        usage_count=0,
        cluster_id="",
        expected_value=0.6,
        variance=0.0,
        recent_scores=[0.5],
        marginal_effect=0.0,
        sensitivity_score=0.0,
        counterfactual_deltas=[],
        avg_counterfactual_delta=0.0,
        tags=[],
    )
    eng_mem._graph.store.append_event(payload)
    ef._graph.store.append_event(payload)

    text = "shared deterministic input phrase"
    p_m = eng_mem.process(text).policy_state
    p_f = ef.process(text).policy_state

    assert json.dumps(p_m, sort_keys=True, default=str) == json.dumps(
        p_f, sort_keys=True, default=str
    )


class _FixedScores(ModelProvider):
    def predict_expected_score(
        self, input_text, candidate_memories, *, model_variant: str = "baseline"
    ) -> float:
        return 0.11

    def evaluate(self, input_text, output_text, context=None):
        return {
            "score": 0.22,
            "components": {"relevance": 0.3, "coherence": 0.3, "usefulness": 0.4},
            "confidence": 0.9,
        }


def test_model_provider_mock_changes_scores(tmp_path):
    cfg = RuntimeConfig(deterministic_clock=True, deterministic_seed=1)
    mock = MagicMock(wraps=_FixedScores())

    ef = Engine.with_file_backend(
        agent_id="agent", config=cfg, root_dir=tmp_path, model=mock
    )
    ef.process("mocked evaluator path")

    mock.predict_expected_score.assert_called()
    mock.evaluate.assert_called()


def test_resonance_graph_accepts_explicit_storage_backend(tmp_path):
    from rwmg.storage import FileStorageBackend

    be = FileStorageBackend(tmp_path, "agent-x")
    g = ResonanceWeightedMemoryGraph(
        agent_id="agent-x",
        root_dir=tmp_path,
        storage_backend=be,
        deterministic_clock=True,
    )
    g.process("hello storage abstraction")
    assert be.store_path.exists()
