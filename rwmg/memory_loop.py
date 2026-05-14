"""Phase 6 meta-policy self-modeling layer for the memory loop.

The loop remains intentionally isolated:

retrieve candidates -> compare strategies and model variants -> counterfactual
attribution -> regret/meta-error-aware selection -> generate -> evaluate
-> update -> causal credit -> update self-model -> persist trace

``memory_store.json`` is the single canonical store for event state, cached
embeddings, expected values, strategy clusters, policy state, self-model
calibration data, and traces.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import logging
import math
import re
import time

import numpy as np
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("rwmg.policy")

from rwmg.circuit_breaker import PolicyCircuitBreaker
from rwmg.runtime_state import (
    ExecutionTrace,
    RuntimeState,
    derive_global_seed,
    policy_state_fingerprint,
)
from rwmg.policy_context import (
    async_model_provider_scope,
    get_active_model_provider,
    model_provider_scope,
)
from rwmg.storage import FileStorageBackend, InMemoryStorageBackend, StorageBackend


SCHEMA_VERSION = "1.0.0"
EMBEDDING_VERSION = "rwmg_sparse_tfidf_v1"

MEMORY_STORE_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "agent_id",
        "events",
        "event_log",
        "feedback_log",
        "process_traces",
        "clusters",
        "policy_state",
        "self_model",
        "storage_revision",
    }
)

DEFAULT_MAX_MEMORY_ENTRIES = 5000
DEFAULT_AGENT_ID = "default"


_GIT_REVISION_CACHE: Optional[str] = None


def memory_store_hash(store: Dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON snapshot (sorted keys) for replay / audit."""

    blob = json.dumps(store, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _git_revision() -> str:
    global _GIT_REVISION_CACHE
    if _GIT_REVISION_CACHE is not None:
        return _GIT_REVISION_CACHE
    try:
        import subprocess

        root = Path(__file__).resolve().parents[1]
        _GIT_REVISION_CACHE = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        _GIT_REVISION_CACHE = ""
    return _GIT_REVISION_CACHE


def fallback_policy_output(
    input: str,
    candidates: Sequence[RetrievedMemory],
    policy_state: Dict,
    *,
    predicted_score: float = 0.0,
) -> str:
    """Deterministic baseline output using the strongest-weight retrieved memory."""

    task = _normalized_task(input)
    preferred = policy_state.get("preferred_patterns", {})
    preferred_terms = sorted(preferred, key=lambda term: (-float(preferred[term]), term))[:3]
    preferred_text = ", ".join(preferred_terms) if preferred_terms else "clear"
    if not candidates:
        return (
            f"Task: {task}\n"
            "Answer: clear baseline response.\n"
            "Plan: identify context, produce one useful step."
        )
    best = max(candidates, key=lambda m: (m.event.weight, m.event.expected_value, m.event.id))
    dominant = _strip_generated_sections(best.event.output)
    if len(candidates) >= 2 or "sequence" in _concept_terms(input) or predicted_score > 0.55:
        return (
            f"Task: {task}\n"
            "Answer: concise, structured, specific, actionable.\n"
            "Steps: 1. assess context 2. apply predicted strategy 3. verify outcome.\n"
            f"Policy: prefer {preferred_text}.\n"
            f"Pattern: {dominant}"
        )
    return (
        f"Task: {task}\n"
        "Answer: clear, structured, specific.\n"
        f"Policy: prefer {preferred_text}.\n"
        f"Pattern: {dominant}"
    )
DEFAULT_CLUSTER_THRESHOLD = 0.58
DEFAULT_EPSILON = 0.2
DEFAULT_GAMMA = 0.5
DEFAULT_LEARNING_RATE = 0.35
DEFAULT_MAX_MEMORIES = 3
DEFAULT_MAX_WEIGHT = 1.0
DEFAULT_MEMORY_TOKEN_CAP = 120
DEFAULT_MIN_WEIGHT = -1.0
DEFAULT_RETENTION_FACTOR = 0.92
DEFAULT_TEMPORAL_WINDOW = 4
DEFAULT_THRESHOLD = 0.05
EXPLORATION_MIN = 0.05
EXPLORATION_MAX = 0.4
EXPLORATION_VARIANCE_SCALE = 4.0
REGRET_WEIGHT = 0.35
META_ERROR_WEIGHT = 0.25

MODEL_VARIANTS = [
    "baseline",
    "low_bias",
    "high_sensitivity",
    "low_regret_weight",
    "high_regret_weight",
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
}

_CONCEPT_ALIASES = {
    "automobile": "vehicle",
    "auto": "vehicle",
    "car": "vehicle",
    "vehicle": "vehicle",
    "brief": "summarize",
    "condense": "summarize",
    "recap": "summarize",
    "summary": "summarize",
    "summarize": "summarize",
    "compose": "write",
    "create": "write",
    "draft": "write",
    "write": "write",
    "effective": "quality",
    "improve": "quality",
    "improves": "quality",
    "quality": "quality",
    "random": "noise",
    "chaos": "noise",
    "noise": "noise",
    "guideline": "policy",
    "guidelines": "policy",
    "policy": "policy",
    "rule": "policy",
    "rules": "policy",
    "memory": "memory",
    "recall": "memory",
    "remember": "memory",
    "resonance": "memory",
    "retention": "memory",
    "photovoltaic": "solar",
    "solar": "solar",
    "sun": "solar",
    "answer": "answer",
    "reply": "answer",
    "response": "answer",
    "checklist": "structured",
    "outline": "structured",
    "steps": "structured",
    "structured": "structured",
    "safe": "safety",
    "safety": "safety",
    "unsafe": "safety",
    "plan": "plan",
    "roadmap": "plan",
    "strategy": "plan",
    "sequence": "sequence",
    "sequential": "sequence",
    "multi": "sequence",
}

_SUCCESS_TERMS = {"actionable", "clear", "concise", "specific", "structured", "verify"}
_FAILURE_TERMS = {"bad", "noise", "ramble", "random", "unsafe", "vague"}


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    agent_id: str
    input: str
    output: str
    outcome_signal: float
    weight: float
    timestamp: int
    type: str = "interaction"
    embedding: Dict[str, float] = field(default_factory=dict)
    future_score: float = 0.0
    usage_count: int = 0
    cluster_id: str = ""
    expected_value: float = 0.0
    variance: float = 0.0
    recent_scores: List[float] = field(default_factory=list)
    marginal_effect: float = 0.0
    sensitivity_score: float = 0.0
    counterfactual_deltas: List[float] = field(default_factory=list)
    avg_counterfactual_delta: float = 0.0
    tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievedMemory:
    event: MemoryEvent
    similarity: float
    score: float
    diversity_factor: float
    cluster_weight: float
    expected_value: float


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _concept_terms(text: str) -> List[str]:
    terms: List[str] = []
    for token in _tokens(text):
        if token in _STOPWORDS:
            continue
        terms.append(_CONCEPT_ALIASES.get(token, token))
    return terms


def _static_idf(term: str) -> float:
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(term))
    return 1.0 + (checksum % 17) / 10.0


@functools.lru_cache(maxsize=4096)
def _embed_cached_terms(text: str) -> Tuple[Tuple[str, float], ...]:
    """Frozen embedding vector for LRU caching (tuple of sorted term, weight pairs)."""

    terms = _concept_terms(text)
    if not terms:
        return ()
    counts: Dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    total = float(len(terms))
    return tuple(
        (term, (count / total) * _static_idf(term)) for term, count in sorted(counts.items())
    )


def embed(text: str) -> Dict[str, float]:
    """Return deterministic semantic TF-IDF-style sparse vector."""

    return dict(_embed_cached_terms(text))


def cosine_similarity(left: Dict[str, float], right: Dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left[term] * right[term] for term in sorted(set(left) & set(right)))
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


RETRIEVAL_RANK_TOL = 1e-6


def _np_union_vocab_dense(
    query_embedding: Dict[str, float],
    candidate_events: Sequence[MemoryEvent],
) -> Tuple[np.ndarray, np.ndarray, List[MemoryEvent], Tuple[str, ...]]:
    """Dense stack in ``R^{|U|}`` with ``U = sorted(keys(q) ∪ keys(e) for all e)`` (pairwise-correct cosine)."""

    keys: set[str] = set(query_embedding)
    for event in candidate_events:
        keys.update(event.embedding)
    vocab = tuple(sorted(keys))
    if not vocab:
        return np.zeros((0, 0), dtype=np.float64), np.zeros((0,), dtype=np.float64), [], ()
    idx = {t: i for i, t in enumerate(vocab)}
    d = len(vocab)
    q = np.zeros((d,), dtype=np.float64)
    for term, weight in query_embedding.items():
        q[idx[term]] = float(weight)
    rows: List[MemoryEvent] = []
    mat: List[List[float]] = []
    for event in candidate_events:
        row = [0.0] * d
        for term, weight in event.embedding.items():
            if term in idx:
                row[idx[term]] = float(weight)
        mat.append(row)
        rows.append(event)
    return np.asarray(mat, dtype=np.float64), q, rows, vocab


def _np_batch_cosine_similarities(
    query_embedding: Dict[str, float],
    candidate_events: Sequence[MemoryEvent],
) -> np.ndarray:
    m, q, _, _ = _np_union_vocab_dense(query_embedding, candidate_events)
    if m.size == 0:
        return np.zeros((0,), dtype=np.float64)
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return np.zeros((m.shape[0],), dtype=np.float64)
    mn = np.linalg.norm(m, axis=1)
    dots = m @ q
    return np.divide(dots, mn * qn, out=np.zeros_like(dots), where=(mn * qn) > 0.0)


def similarity(left: str, right: str) -> float:
    return cosine_similarity(embed(left), embed(right))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _clamp_signal(signal: float) -> float:
    return _clamp(signal, -1.0, 1.0)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _recency_decay(timestamp: int, *, now: Optional[int] = None) -> float:
    reference = int(time.time()) if now is None else int(now)
    age_seconds = max(0, reference - int(timestamp or 0))
    return math.exp(-age_seconds / 3600.0)


def _memory_expected_value_payload(payload: Dict, *, now: Optional[int] = None) -> float:
    scores = [float(score) for score in payload.get("recent_scores", [])[-8:]]
    if scores:
        recent_success = _clamp((_mean(scores) + 1.0) / 2.0, 0.0, 1.0)
        stability_factor = _clamp(1.0 - _variance(scores), 0.0, 1.0)
    else:
        fallback = max(
            float(payload.get("weight", 0.0)),
            float(payload.get("future_score", 0.0)),
            float(payload.get("outcome_signal", 0.0)),
            0.0,
        )
        recent_success = _clamp(fallback, 0.0, 1.0)
        stability_factor = 1.0
    recency_decay = _recency_decay(int(payload.get("timestamp", 0)), now=now)
    return _clamp(recent_success * 0.5 + stability_factor * 0.3 + recency_decay * 0.2, 0.0, 1.0)


def _refresh_memory_value(payload: Dict, *, now: Optional[int] = None) -> None:
    scores = [float(score) for score in payload.get("recent_scores", [])[-8:]]
    payload["recent_scores"] = scores
    payload["variance"] = _variance(scores)
    payload["expected_value"] = _memory_expected_value_payload(payload, now=now)
    deltas = [abs(float(delta)) for delta in payload.get("counterfactual_deltas", [])[-8:]]
    payload["counterfactual_deltas"] = deltas
    payload["avg_counterfactual_delta"] = _mean(deltas)
    prior_sensitivity = float(payload.get("sensitivity_score", 0.0))
    payload["sensitivity_score"] = _clamp(
        prior_sensitivity * 0.7 + payload["avg_counterfactual_delta"] * 0.3,
        0.0,
        1.0,
    )


def _policy_state(exploration_rate: float = DEFAULT_EPSILON) -> Dict:
    return {
        "preferred_patterns": {},
        "suppressed_patterns": {},
        "exploration_rate": exploration_rate,
        "evaluation_trend": [],
        "cluster_performance": {},
        "prediction_error_moving_avg": 0.0,
    }


def _self_model_state() -> Dict:
    return {
        "prediction_bias": 0.0,
        "regret_bias": 0.0,
        "causal_attribution_bias": 0.0,
        "calibration_error": 0.0,
        "confidence_drift": 0.0,
        "global_calibration_error": 0.0,
        "model_rankings": {variant: 0.5 for variant in MODEL_VARIANTS},
        "drift_trend": 0.0,
        "meta_score_history": [],
        "prediction_error_history": [],
        "regret_error_history": [],
        "attribution_error_history": [],
        "confidence_drift_history": [],
    }


def _decay_toward_zero(value: float, factor: float = 0.9) -> float:
    return float(value) * factor


def validate_schema(store: Dict) -> None:
    """Validate required version; warn on unknown top-level keys (forward compatibility)."""

    if not isinstance(store, dict):
        raise ValueError("memory store root must be a dict")
    extra = set(store.keys()) - MEMORY_STORE_TOP_LEVEL_KEYS
    if extra:
        logger.warning("memory store has unrecognized top-level keys (ignored): %s", sorted(extra))
    version = store.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {version!r} (expected {SCHEMA_VERSION})")


def _migrate_legacy_memory_store(store: Dict) -> None:
    """Assign schema_version to legacy files (same on-disk shape as current schema)."""

    if not store.get("schema_version"):
        store["schema_version"] = SCHEMA_VERSION


def _event_from_payload(payload: Dict, *, now: Optional[int] = None) -> MemoryEvent:
    upgraded = {
        "id": payload.get("id", ""),
        "agent_id": payload.get("agent_id", DEFAULT_AGENT_ID),
        "input": payload.get("input", ""),
        "output": payload.get("output", ""),
        "outcome_signal": float(payload.get("outcome_signal", 0.0)),
        "weight": float(payload.get("weight", 0.0)),
        "timestamp": int(payload.get("timestamp", 0)),
        "type": payload.get("type", "interaction"),
        "embedding": payload.get("embedding") or embed(payload.get("input", "")),
        "future_score": float(payload.get("future_score", 0.0)),
        "usage_count": int(payload.get("usage_count", 0)),
        "cluster_id": payload.get("cluster_id", ""),
        "expected_value": float(
            payload.get("expected_value", _memory_expected_value_payload(payload, now=now))
        ),
        "variance": float(payload.get("variance", 0.0)),
        "recent_scores": [float(score) for score in payload.get("recent_scores", [])[-8:]],
        "marginal_effect": float(payload.get("marginal_effect", 0.0)),
        "sensitivity_score": float(payload.get("sensitivity_score", 0.0)),
        "counterfactual_deltas": [
            float(delta) for delta in payload.get("counterfactual_deltas", [])[-8:]
        ],
        "avg_counterfactual_delta": float(payload.get("avg_counterfactual_delta", 0.0)),
        "tags": [str(t) for t in payload.get("tags", [])] if isinstance(payload.get("tags"), list) else [],
    }
    return MemoryEvent(**upgraded)


def _prune_priority_score(payload: Dict[str, Any], *, now_ts: int) -> float:
    """Higher score = more valuable; lowest scores are pruned first."""

    if payload.get("type") == "core_identity":
        return 1e12
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    if any(str(t) == "core_identity" for t in tags):
        return 1e12
    w = _clamp(float(payload.get("weight", 0.0)), -1.0, 1.0)
    w_norm = (w + 1.0) / 2.0
    rec = float(_recency_decay(int(payload.get("timestamp", 0)), now=now_ts))
    usage = min(float(payload.get("usage_count", 0)) / 50.0, 1.0)
    ev = abs(float(payload.get("expected_value", 0.0)))
    marginal = abs(float(payload.get("avg_counterfactual_delta", 0.0)))
    strategic = _clamp(ev * 0.6 + marginal * 0.4, 0.0, 1.0)
    return w_norm * 0.5 + rec * 0.2 + usage * 0.2 + strategic * 0.1


class MemoryStore:
    """Coordinates policy state via a ``StorageBackend`` (file, memory, Redis, etc.)."""

    def __init__(
        self,
        storage: Union[StorageBackend, Path, str],
        agent_id: str = DEFAULT_AGENT_ID,
        *,
        cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
        exploration_rate: float = DEFAULT_EPSILON,
        max_memory_entries: int = DEFAULT_MAX_MEMORY_ENTRIES,
    ):
        self.agent_id = agent_id
        if isinstance(storage, (Path, str)):
            self.backend: StorageBackend = FileStorageBackend(Path(storage), agent_id)
        else:
            self.backend = storage
        self.cluster_threshold = cluster_threshold
        self.exploration_rate = exploration_rate
        self.max_memory_entries = int(max_memory_entries)
        self._reference_now: Optional[int] = None
        self._cluster_layout_dirty = True
        self._store_epoch = 0
        snapshot = self.backend.load()
        if snapshot is None:
            self._write_store(self._empty_store())

    def set_reference_time(self, ts: Optional[int]) -> None:
        """When set, recency and new event timestamps use ``ts`` instead of wall clock."""

        self._reference_now = int(ts) if ts is not None else None

    def _eval_now(self) -> int:
        return int(self._reference_now if self._reference_now is not None else time.time())

    def _empty_store(self) -> Dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "storage_revision": 0,
            "events": {},
            "event_log": [],
            "feedback_log": [],
            "process_traces": [],
            "clusters": {},
            "policy_state": _policy_state(self.exploration_rate),
            "self_model": _self_model_state(),
        }

    def load_store(self) -> Dict:
        raw = self.backend.load()
        store = raw if isinstance(raw, dict) else None
        if store is None:
            store = self._empty_store()
        _migrate_legacy_memory_store(store)
        store.setdefault("agent_id", self.agent_id)
        store.setdefault("events", {})
        store.setdefault("event_log", [])
        store.setdefault("feedback_log", [])
        store.setdefault("process_traces", [])
        store.setdefault("clusters", {})
        store.setdefault("policy_state", _policy_state(self.exploration_rate))
        store.setdefault("self_model", _self_model_state())
        store.setdefault("storage_revision", 0)
        self_model = store["self_model"]
        self_model.setdefault("model_rankings", {variant: 0.5 for variant in MODEL_VARIANTS})
        for variant in MODEL_VARIANTS:
            self_model["model_rankings"].setdefault(variant, 0.5)
        self_model.setdefault("prediction_bias", 0.0)
        self_model.setdefault("regret_bias", 0.0)
        self_model.setdefault("causal_attribution_bias", 0.0)
        self_model.setdefault("calibration_error", 0.0)
        self_model.setdefault("confidence_drift", 0.0)
        self_model.setdefault("global_calibration_error", 0.0)
        self_model.setdefault("drift_trend", 0.0)
        self_model.setdefault("meta_score_history", [])
        self_model.setdefault("prediction_error_history", [])
        self_model.setdefault("regret_error_history", [])
        self_model.setdefault("attribution_error_history", [])
        self_model.setdefault("confidence_drift_history", [])
        policy = store["policy_state"]
        policy.setdefault("preferred_patterns", {})
        policy.setdefault("suppressed_patterns", {})
        policy.setdefault("exploration_rate", self.exploration_rate)
        policy.setdefault("evaluation_trend", [])
        policy.setdefault("cluster_performance", {})
        policy.setdefault("prediction_error_moving_avg", 0.0)
        try:
            validate_schema(store)
        except ValueError as exc:
            logger.error("memory store schema validation failed: %s", exc)
            raise
        if len(store.get("events") or {}) > self.max_memory_entries:
            logger.error(
                "memory store has %s events (max_memory_entries=%s); pruning now",
                len(store["events"]),
                self.max_memory_entries,
            )
            self.prune_memory(store)
            self._write_store(store)
        return store

    def _ensure_cluster_layout_fresh(self) -> None:
        if not self._cluster_layout_dirty:
            return
        store = self.load_store()
        self._rebuild_clusters(store)
        self._cluster_layout_dirty = False
        self._write_store(store)

    def load_state(self) -> Dict[str, Dict]:
        events = self.load_store()["events"]
        return events if isinstance(events, dict) else {}

    def load_clusters(self) -> Dict[str, Dict]:
        self._ensure_cluster_layout_fresh()
        clusters = self.load_store()["clusters"]
        return clusters if isinstance(clusters, dict) else {}

    def load_policy_state(self) -> Dict:
        return self.load_store()["policy_state"]

    def load_self_model(self) -> Dict:
        return self.load_store()["self_model"]

    def update_self_model(
        self,
        meta_result: Dict,
        model_selected: str,
        predicted_confidence: float,
    ) -> Dict:
        store = self.load_store()
        self_model = store["self_model"]
        prediction_error = float(meta_result.get("prediction_error", 0.0))
        prediction_bias_error = float(meta_result.get("signed_prediction_error", prediction_error))
        regret_error = float(meta_result.get("regret_error", 0.0))
        attribution_error = float(meta_result.get("attribution_error", 0.0))
        meta_score = float(meta_result.get("overall_meta_score", 0.0))
        actual_accuracy = 1.0 - min(1.0, prediction_error)
        confidence_drift = abs(float(predicted_confidence) - actual_accuracy)

        self_model["prediction_bias"] = _decay_toward_zero(
            float(self_model.get("prediction_bias", 0.0)) * 0.75 + prediction_bias_error * 0.25
        )
        self_model["regret_bias"] = _decay_toward_zero(
            float(self_model.get("regret_bias", 0.0)) * 0.75 + regret_error * 0.25
        )
        self_model["causal_attribution_bias"] = _decay_toward_zero(
            float(self_model.get("causal_attribution_bias", 0.0)) * 0.75 + attribution_error * 0.25
        )
        residual_error = min(1.0, abs(prediction_bias_error))
        self_model["calibration_error"] = (
            float(self_model.get("calibration_error", 0.0)) * 0.6 + residual_error * 0.4
        )
        self_model["confidence_drift"] = (
            float(self_model.get("confidence_drift", 0.0)) * 0.7 + confidence_drift * 0.3
        )
        self_model["global_calibration_error"] = (
            float(self_model.get("global_calibration_error", 0.0)) * 0.75
            + meta_score * 0.25
        )

        for key, value in (
            ("meta_score_history", meta_score),
            ("prediction_error_history", prediction_error),
            ("regret_error_history", regret_error),
            ("attribution_error_history", attribution_error),
            ("confidence_drift_history", confidence_drift),
        ):
            history = [float(item) for item in self_model.get(key, [])[-19:]]
            history.append(float(value))
            self_model[key] = history
        self_model["drift_trend"] = _mean(self_model["confidence_drift_history"][-5:])

        rankings = self_model.setdefault("model_rankings", {})
        for variant in MODEL_VARIANTS:
            rankings.setdefault(variant, 0.5)
        rankings[model_selected] = (
            float(rankings.get(model_selected, 0.5)) * 0.75 + meta_score * 0.25
        )

        store["self_model"] = self_model
        self._write_store(store)
        return json.loads(json.dumps(self_model))

    def _write_store(self, store: Dict) -> None:
        self.backend.save(store)
        self._store_epoch += 1

    def append_event(self, event: MemoryEvent) -> None:
        store = self.load_store()
        payload = asdict(event)
        if not payload.get("embedding"):
            payload["embedding"] = embed(payload.get("input", ""))
        if not payload.get("cluster_id"):
            payload["cluster_id"] = self._assign_cluster_id(store, payload["embedding"])
        now_ts = self._eval_now()
        _refresh_memory_value(payload, now=now_ts)
        store["events"][event.id] = payload
        store["event_log"].append(payload.copy())
        self._rebuild_clusters(store)
        self._cluster_layout_dirty = False
        self.prune_memory(store)
        self._write_store(store)

    def prune_memory(self, store: Optional[Dict] = None) -> int:
        """Drop lowest multi-factor priority events until at most ``max_memory_entries`` remain."""

        store = store if store is not None else self.load_store()
        events = store["events"]
        if len(events) <= self.max_memory_entries:
            return 0
        now_ts = self._eval_now()
        ranked: List[Tuple[float, int, str]] = []
        for event_id, payload in sorted(events.items()):
            score = _prune_priority_score(payload, now_ts=now_ts)
            ranked.append((score, int(payload.get("timestamp", 0)), event_id))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]))
        remove_count = len(events) - self.max_memory_entries
        remove_ids = {row[2] for row in ranked[:remove_count]}
        for event_id in remove_ids:
            del events[event_id]
        store["event_log"] = [row for row in store["event_log"] if row.get("id") not in remove_ids]
        self._rebuild_clusters(store)
        self._cluster_layout_dirty = False
        return remove_count

    def create_event(self, input: str, output: str, *, event_type: str = "interaction") -> MemoryEvent:
        store = self.load_store()
        event_embedding = embed(input)
        cluster_id = self._assign_cluster_id(store, event_embedding)
        seq = len(store["event_log"])
        digest = hashlib.sha256(
            f"{self.agent_id}:{seq}:{input}:{output}".encode("utf-8")
        ).hexdigest()
        event_id = digest[:32]
        now_ts = self._eval_now()
        payload = {
            "id": event_id,
            "agent_id": self.agent_id,
            "input": input,
            "output": output,
            "outcome_signal": 0.0,
            "weight": 0.0,
            "timestamp": now_ts,
            "type": event_type,
            "embedding": event_embedding,
            "future_score": 0.0,
            "usage_count": 0,
            "cluster_id": cluster_id,
            "expected_value": 0.0,
            "variance": 0.0,
            "recent_scores": [],
            "marginal_effect": 0.0,
            "sensitivity_score": 0.0,
            "counterfactual_deltas": [],
            "avg_counterfactual_delta": 0.0,
            "tags": [],
        }
        _refresh_memory_value(payload, now=now_ts)
        event = MemoryEvent(**payload)
        self.append_event(event)
        return event

    def events(self) -> List[MemoryEvent]:
        self._ensure_cluster_layout_fresh()
        store = self.load_store()
        changed = False
        now_ts = self._eval_now()
        for event_id in sorted(store["events"]):
            payload = store["events"][event_id]
            before = (
                payload.get("expected_value"),
                payload.get("variance"),
                tuple(payload.get("recent_scores", [])),
            )
            _refresh_memory_value(payload, now=now_ts)
            after = (
                payload.get("expected_value"),
                payload.get("variance"),
                tuple(payload.get("recent_scores", [])),
            )
            changed = changed or before != after
        if changed:
            self._rebuild_clusters(store)
            self._cluster_layout_dirty = False
            self._write_store(store)
        events = [
            _event_from_payload(store["events"][eid], now=now_ts) for eid in sorted(store["events"])
        ]
        events.sort(key=lambda event: (event.timestamp, event.id))
        return events

    def update_weight(
        self,
        event_id: str,
        signal: float,
        *,
        retention_factor: float = DEFAULT_RETENTION_FACTOR,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> Tuple[float, float]:
        signal = _clamp_signal(signal)
        store = self.load_store()
        if event_id not in store["events"]:
            raise KeyError(f"memory event not found: {event_id}")

        event = store["events"][event_id]
        before = float(event.get("weight", 0.0))
        multiplier = 1.2 if event.get("type", "interaction") == "feedback" else 1.0
        after = _clamp(before * retention_factor + learning_rate * signal * multiplier, min_w, max_w)
        event["weight"] = after
        event["outcome_signal"] = signal
        event.setdefault("embedding", embed(event.get("input", "")))
        scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
        scores.append(signal)
        event["recent_scores"] = scores
        now_ts = self._eval_now()
        _refresh_memory_value(event, now=now_ts)
        store["feedback_log"].append(
            {
                "event_id": event_id,
                "agent_id": self.agent_id,
                "signal": signal,
                "weight_before": before,
                "weight_after": after,
                "timestamp": now_ts,
                "type": "direct",
            }
        )
        if abs(after - before) > 0.02:
            self._rebuild_clusters(store)
            self._cluster_layout_dirty = False
        else:
            self._cluster_layout_dirty = True
        self._write_store(store)
        return before, after

    def update_counterfactual_attribution(
        self,
        attribution: Dict[str, float],
        reward: float,
        *,
        gamma: float = DEFAULT_GAMMA,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> List[Dict]:
        store = self.load_store()
        updates: List[Dict] = []
        reward = _clamp_signal(reward)
        now_ts = self._eval_now()
        for event_id, marginal_effect in sorted(attribution.items()):
            if event_id not in store["events"]:
                continue
            event = store["events"][event_id]
            before = float(event.get("weight", 0.0))
            sensitivity_before = float(event.get("sensitivity_score", 0.0))
            contribution_score = _clamp(
                float(marginal_effect) * max(float(event.get("weight", 0.0)), 0.0),
                -1.0,
                1.0,
            )
            after = _clamp(before + gamma * reward * contribution_score, min_w, max_w)
            event["weight"] = after
            event["marginal_effect"] = float(marginal_effect)
            event["sensitivity_score"] = _clamp(
                sensitivity_before * 0.7 + abs(float(marginal_effect)) * 0.3,
                0.0,
                1.0,
            )
            deltas = [float(delta) for delta in event.get("counterfactual_deltas", [])[-7:]]
            deltas.append(float(marginal_effect))
            event["counterfactual_deltas"] = deltas
            event["avg_counterfactual_delta"] = _mean(deltas)
            scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
            scores.append(reward * contribution_score)
            event["recent_scores"] = scores
            _refresh_memory_value(event, now=now_ts)
            updates.append(
                {
                    "event_id": event_id,
                    "marginal_effect": float(marginal_effect),
                    "contribution_score": contribution_score,
                    "weight_before": before,
                    "weight_after": after,
                }
            )
        self._cluster_layout_dirty = True
        self._write_store(store)
        return updates

    def decay_weights(
        self,
        retention_factor: float,
        *,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        store = self.load_store()
        before = {
            event_id: float(store["events"][event_id].get("weight", 0.0))
            for event_id in sorted(store["events"])
        }
        now_ts = self._eval_now()
        for event_id in sorted(store["events"]):
            event = store["events"][event_id]
            event["weight"] = _clamp(float(event.get("weight", 0.0)) * retention_factor, min_w, max_w)
            event.setdefault("embedding", embed(event.get("input", "")))
            _refresh_memory_value(event, now=now_ts)
        after = {
            event_id: float(store["events"][event_id].get("weight", 0.0))
            for event_id in sorted(store["events"])
        }
        self._cluster_layout_dirty = True
        self._write_store(store)
        return before, after

    def record_usage(self, event_ids: Sequence[str]) -> None:
        store = self.load_store()
        now_ts = self._eval_now()
        for event_id in event_ids:
            if event_id in store["events"]:
                event = store["events"][event_id]
                event["usage_count"] = int(event.get("usage_count", 0)) + 1
                _refresh_memory_value(event, now=now_ts)
        self._cluster_layout_dirty = True
        self._write_store(store)

    def apply_temporal_credit(
        self,
        reward: float,
        current_event_id: str,
        current_input: str,
        *,
        window_size: int = DEFAULT_TEMPORAL_WINDOW,
        gamma: float = DEFAULT_GAMMA,
        marginal_effects: Optional[Dict[str, float]] = None,
        contribution_threshold: float = 0.02,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> List[Dict]:
        reward = _clamp_signal(reward)
        marginal_effects = marginal_effects or {}
        store = self.load_store()
        now_ts = self._eval_now()
        prior_ids = [
            row["id"]
            for row in store["event_log"]
            if row.get("id") != current_event_id and row.get("id") in store["events"]
        ][-window_size:]
        updates: List[Dict] = []
        for event_id in prior_ids:
            event = store["events"][event_id]
            marginal_effect = float(marginal_effects.get(event_id, 0.0))
            contribution = similarity(event.get("input", ""), current_input) * float(event.get("weight", 0.0)) * marginal_effect
            if contribution <= contribution_threshold:
                continue
            recent_scores = [float(score) for score in event.get("recent_scores", [])[-4:]]
            output_terms = set(_tokens(event.get("output", "")))
            known_negative = (
                float(event.get("outcome_signal", 0.0)) < 0.0
                or (recent_scores and _mean(recent_scores) < 0.0)
                or bool(output_terms & _FAILURE_TERMS)
            )
            if reward > 0.0 and known_negative:
                continue
            before = float(event.get("weight", 0.0))
            prior_future = float(event.get("future_score", 0.0))
            future_score = _clamp(prior_future * 0.5 + reward * contribution, -1.0, 1.0)
            after = _clamp(before + gamma * future_score, min_w, max_w)
            event["future_score"] = future_score
            event["weight"] = after
            event["marginal_effect"] = marginal_effect
            event["sensitivity_score"] = _clamp(
                float(event.get("sensitivity_score", 0.0)) * 0.7 + abs(marginal_effect) * 0.3,
                0.0,
                1.0,
            )
            scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
            scores.append(reward * contribution)
            event["recent_scores"] = scores
            _refresh_memory_value(event, now=now_ts)
            updates.append(
                {
                    "event_id": event_id,
                    "contribution": contribution,
                    "marginal_effect": marginal_effect,
                    "future_score": future_score,
                    "weight_before": before,
                    "weight_after": after,
                }
            )
            store["feedback_log"].append(
                {
                    "event_id": event_id,
                    "agent_id": self.agent_id,
                    "signal": reward,
                    "contribution": contribution,
                    "weight_before": before,
                    "weight_after": after,
                    "timestamp": now_ts,
                    "type": "causal_temporal_credit",
                }
            )
        self._cluster_layout_dirty = True
        self._write_store(store)
        return updates

    def update_policy_state(
        self,
        evaluation: Dict,
        event: MemoryEvent,
        retrieved: Sequence[RetrievedMemory],
        *,
        exploration_rate: float,
        prediction_error: float = 0.0,
    ) -> Dict:
        store = self.load_store()
        policy = store["policy_state"]
        score = float(evaluation["score"])
        policy["exploration_rate"] = exploration_rate
        alpha = 0.15
        prev_ema = float(policy.get("prediction_error_moving_avg", 0.0))
        policy["prediction_error_moving_avg"] = (1.0 - alpha) * prev_ema + alpha * float(prediction_error)
        trend = list(policy.get("evaluation_trend", []))
        trend.append(score)
        policy["evaluation_trend"] = trend[-30:]

        target = policy["preferred_patterns"] if score >= 0.2 else policy["suppressed_patterns"]
        for term in _pattern_terms(event.output):
            target[term] = round(float(target.get(term, 0.0)) + abs(score), 6)

        cluster_ids = {memory.event.cluster_id for memory in retrieved if memory.event.cluster_id}
        if event.cluster_id:
            cluster_ids.add(event.cluster_id)
        clusters = store.get("clusters", {})
        for cluster_id in sorted(cluster_ids):
            if not cluster_id:
                continue
            current = policy["cluster_performance"].get(cluster_id, {"score": 0.0, "count": 0})
            count = int(current.get("count", 0)) + 1
            previous_score = float(current.get("score", 0.0))
            averaged = previous_score + (score - previous_score) / count
            policy["cluster_performance"][cluster_id] = {
                "score": round(averaged, 6),
                "count": count,
                "expected_value": float(clusters.get(cluster_id, {}).get("expected_value", 0.0)),
            }

        store["policy_state"] = policy
        self._write_store(store)
        return json.loads(json.dumps(policy))

    def append_trace(self, trace: Dict) -> None:
        store = self.load_store()
        store["process_traces"].append(trace)
        self._write_store(store)

    def read_log(self) -> List[Dict]:
        return list(self.load_store()["event_log"])

    def read_traces(self) -> List[Dict]:
        return list(self.load_store()["process_traces"])

    def policy_stability(self) -> float:
        selections = [
            trace.get("strategy_selected", "")
            for trace in self.read_traces()[-12:]
            if trace.get("strategy_selected")
        ]
        if len(selections) < 2:
            return 0.0
        unique = sorted(set(selections))
        if len(unique) <= 1:
            return 0.0
        encoded = [unique.index(selection) / (len(unique) - 1) for selection in selections]
        return _variance(encoded)

    def _assign_cluster_id(self, store: Dict, embedding: Dict[str, float]) -> str:
        clusters = store.get("clusters") or {}
        best_cluster = ""
        best_similarity = -1.0
        for cluster_id in sorted(clusters):
            cluster = clusters[cluster_id]
            sim = cosine_similarity(embedding, cluster.get("centroid", {}))
            if sim > best_similarity:
                best_similarity = sim
                best_cluster = cluster_id
        if best_cluster and best_similarity >= self.cluster_threshold:
            return best_cluster

        index = len(clusters) + 1
        cluster_id = f"cluster_{index}"
        while cluster_id in clusters:
            index += 1
            cluster_id = f"cluster_{index}"
        return cluster_id

    def _rebuild_clusters(self, store: Dict) -> None:
        grouped: Dict[str, List[Dict]] = {}
        now_ts = self._eval_now()
        for event_id in sorted(store["events"]):
            event = store["events"][event_id]
            if event.get("type") == "system":
                continue
            event.setdefault("embedding", embed(event.get("input", "")))
            _refresh_memory_value(event, now=now_ts)
            cluster_id = event.get("cluster_id") or "cluster_unassigned"
            grouped.setdefault(cluster_id, []).append(event)

        clusters: Dict[str, Dict] = {}
        for cluster_id, events in sorted(grouped.items(), key=lambda item: item[0]):
            expected_values = [float(event.get("expected_value", 0.0)) for event in events]
            centroid = _average_embedding([event.get("embedding", {}) for event in events])
            representative = max(
                events,
                key=lambda event: (
                    float(event.get("expected_value", 0.0)),
                    float(event.get("weight", 0.0)),
                    -int(event.get("usage_count", 0)),
                    event.get("id", ""),
                ),
            )
            clusters[cluster_id] = {
                "event_ids": [event["id"] for event in events],
                "centroid": centroid,
                "shared_weight": _mean([float(event.get("weight", 0.0)) for event in events]),
                "usage_count": sum(int(event.get("usage_count", 0)) for event in events),
                "representative_id": representative.get("id", ""),
                "expected_value": _mean(expected_values),
                "variance": _variance(expected_values),
                "dominant_memories": [
                    event["id"]
                    for event in sorted(
                        events,
                        key=lambda event: (
                            -float(event.get("expected_value", 0.0)),
                            -float(event.get("weight", 0.0)),
                            event.get("id", ""),
                        ),
                    )[:3]
                ],
            }
        store["clusters"] = clusters


class RetrievalEngine:
    """Rank memories by semantic similarity, weight, and expected value."""

    def __init__(self, max_memories: int = DEFAULT_MAX_MEMORIES, threshold: float = DEFAULT_THRESHOLD):
        self.max_memories = max_memories
        self.threshold = threshold

    def retrieve_reference(
        self,
        input: str,
        events: Sequence[MemoryEvent],
        clusters: Dict[str, Dict],
        *,
        limit: Optional[int] = None,
    ) -> List[RetrievedMemory]:
        """Python reference ranking (used for numerical parity tests)."""

        query_embedding = embed(input)
        scored: List[RetrievedMemory] = []
        for event in events:
            if event.type == "system" or event.weight < self.threshold:
                continue
            sim = cosine_similarity(query_embedding, event.embedding)
            if sim <= 0.0:
                continue
            cluster = clusters.get(event.cluster_id, {})
            cluster_weight = float(cluster.get("shared_weight", 0.0))
            expected_value = max(float(event.expected_value), float(cluster.get("expected_value", 0.0)), 0.01)
            diversity_factor = 1.0 / (1.0 + max(0, event.usage_count))
            score = sim * event.weight * expected_value * diversity_factor
            if score < self.threshold:
                continue
            scored.append(
                RetrievedMemory(
                    event=event,
                    similarity=sim,
                    score=score,
                    diversity_factor=diversity_factor,
                    cluster_weight=cluster_weight,
                    expected_value=expected_value,
                )
            )

        scored.sort(
            key=lambda item: (
                -item.score,
                -item.expected_value,
                -item.similarity,
                -item.event.weight,
                item.event.usage_count,
                item.event.timestamp,
                item.event.id,
            )
        )
        return scored[: (limit or max(self.max_memories * 4, self.max_memories))]

    def retrieve(
        self,
        input: str,
        events: Sequence[MemoryEvent],
        clusters: Dict[str, Dict],
        *,
        limit: Optional[int] = None,
        use_reference: bool = False,
    ) -> List[RetrievedMemory]:
        if use_reference:
            return self.retrieve_reference(input, events, clusters, limit=limit)
        query_embedding = embed(input)
        candidates: List[MemoryEvent] = []
        for event in events:
            if event.type == "system" or event.weight < self.threshold:
                continue
            candidates.append(event)
        if not candidates:
            return []
        sims = _np_batch_cosine_similarities(query_embedding, candidates)
        scored: List[RetrievedMemory] = []
        for sim, event in zip(sims, candidates):
            if float(sim) <= 0.0:
                continue
            cluster = clusters.get(event.cluster_id, {})
            cluster_weight = float(cluster.get("shared_weight", 0.0))
            expected_value = max(float(event.expected_value), float(cluster.get("expected_value", 0.0)), 0.01)
            diversity_factor = 1.0 / (1.0 + max(0, event.usage_count))
            score = float(sim) * event.weight * expected_value * diversity_factor
            if score < self.threshold:
                continue
            scored.append(
                RetrievedMemory(
                    event=event,
                    similarity=float(sim),
                    score=score,
                    diversity_factor=diversity_factor,
                    cluster_weight=cluster_weight,
                    expected_value=expected_value,
                )
            )
        scored.sort(
            key=lambda item: (
                -item.score,
                -item.expected_value,
                -item.similarity,
                -item.event.weight,
                item.event.usage_count,
                item.event.timestamp,
                item.event.id,
            )
        )
        return scored[: (limit or max(self.max_memories * 4, self.max_memories))]


class ContextComposer:
    """Build structured, capped context from selected memories."""

    def __init__(self, memory_token_cap: int = DEFAULT_MEMORY_TOKEN_CAP, max_memories: int = DEFAULT_MAX_MEMORIES):
        self.memory_token_cap = memory_token_cap
        self.max_memories = max_memories

    def compose(self, input: str, memories: Sequence[RetrievedMemory]) -> Tuple[str, List[str]]:
        lines = ["[Relevant Prior Outputs]"]
        used_tokens = len(_tokens(lines[0]))
        memory_ids: List[str] = []

        for index, memory in enumerate(memories[: self.max_memories], start=1):
            output = _strip_generated_sections(memory.event.output)
            candidate = f"{index}. {output}"
            tokens = _tokens(candidate)
            remaining = self.memory_token_cap - used_tokens
            if remaining <= 0:
                break
            if len(tokens) > remaining:
                candidate = " ".join(tokens[:remaining])
                tokens = _tokens(candidate)
            lines.append(candidate)
            used_tokens += len(tokens)
            memory_ids.append(memory.event.id)

        current = f"[Current Task]\n{input}"
        remaining = self.memory_token_cap - used_tokens
        if remaining > 0:
            current_tokens = _tokens(current)
            lines.append(current if len(current_tokens) <= remaining else " ".join(current_tokens[:remaining]))
        return "\n".join(lines), memory_ids


class ResonanceWeightedMemoryGraph:
    """Predictive memory-based policy optimizer."""

    def __init__(
        self,
        *,
        agent_id: str = DEFAULT_AGENT_ID,
        root_dir: Path | str = Path(".rwmg_memory"),
        storage_backend: Optional[StorageBackend] = None,
        max_memories: int = DEFAULT_MAX_MEMORIES,
        memory_token_cap: int = DEFAULT_MEMORY_TOKEN_CAP,
        retention_factor: float = DEFAULT_RETENTION_FACTOR,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
        threshold: float = DEFAULT_THRESHOLD,
        epsilon: float = DEFAULT_EPSILON,
        gamma: float = DEFAULT_GAMMA,
        temporal_window: int = DEFAULT_TEMPORAL_WINDOW,
        deterministic_seed: int = 0,
        production_mode: bool = False,
        deterministic_clock: bool = False,
        max_memory_entries: Optional[int] = None,
        model_provider: Any = None,
        trace_git_revision: Optional[str] = None,
        trace_config_fingerprint: str = "",
        shadow_mode: bool = False,
        gated_exploration: bool = False,
        gated_exploration_epsilon: float = 0.08,
        gated_exploration_confidence_threshold: float = 0.45,
        circuit_failure_threshold: int = 5,
        circuit_cooldown_episodes: int = 8,
    ):
        if not 0.85 <= retention_factor <= 0.98:
            raise ValueError("retention_factor must be in [0.85, 0.98]")
        if not 0.2 <= learning_rate <= 0.5:
            raise ValueError("learning_rate must be in [0.2, 0.5]")
        if not 0.3 <= gamma <= 0.7:
            raise ValueError("gamma must be in [0.3, 0.7]")

        self.agent_id = agent_id
        self.deterministic_seed = deterministic_seed
        self.production_mode = production_mode
        self.deterministic_clock = deterministic_clock
        self.initial_exploration_rate = 0.0 if production_mode else epsilon
        self.gamma = gamma
        self.learning_rate = learning_rate
        self.max_memories = max_memories
        self.max_w = max_w
        self.min_w = min_w
        self.retention_factor = retention_factor
        self.temporal_window = temporal_window
        self.threshold = threshold
        cap = (
            int(max_memory_entries)
            if max_memory_entries is not None
            else DEFAULT_MAX_MEMORY_ENTRIES
        )
        self.trace_git_revision = trace_git_revision
        self.trace_config_fingerprint = trace_config_fingerprint
        self.shadow_mode = shadow_mode
        self.gated_exploration = bool(gated_exploration)
        self.gated_exploration_epsilon = float(gated_exploration_epsilon)
        self.gated_exploration_confidence_threshold = float(gated_exploration_confidence_threshold)
        self._circuit = PolicyCircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            cooldown_episodes=circuit_cooldown_episodes,
        )
        self._global_seed = derive_global_seed(trace_config_fingerprint or "", agent_id)
        self.last_runtime_state: Optional["RuntimeState"] = None
        self._last_evaluation_confidence: float = 1.0
        backend = storage_backend if storage_backend is not None else FileStorageBackend(
            Path(root_dir), agent_id
        )
        self._deterministic_time_base = int(
            hashlib.sha256(f"{agent_id}:{deterministic_seed}".encode("utf-8")).hexdigest()[:12],
            16,
        ) % 900_000_000
        self._process_tick = 0
        self.store = MemoryStore(
            backend,
            agent_id,
            exploration_rate=0.0 if production_mode else epsilon,
            max_memory_entries=cap,
        )
        if model_provider is None:
            from rwmg.model_provider import HeuristicModelProvider

            model_provider = HeuristicModelProvider()
        self.model_provider = model_provider
        self.retrieval = RetrievalEngine(max_memories=max_memories, threshold=threshold)
        self.composer = ContextComposer(memory_token_cap=memory_token_cap, max_memories=max_memories)
        self.last_event_id: Optional[str] = None
        self.last_trace: Optional[Dict] = None

    def _finalize_execution(
        self,
        *,
        input: str,
        output: str,
        event: MemoryEvent,
        memory_hash_before: str,
        t_pipeline: float,
    ) -> None:
        """Persist minimal append-only trace and attach canonical :class:`RuntimeState`."""

        runtime_ms = (time.perf_counter() - t_pipeline) * 1000.0
        mem_after = self.store.load_store()
        pol = self.store.load_policy_state()
        et = ExecutionTrace(
            input_hash=hashlib.sha256(input.encode("utf-8")).hexdigest()[:32],
            output_hash=hashlib.sha256(output.encode("utf-8")).hexdigest()[:32],
            memory_state_hash_before=memory_hash_before,
            memory_state_hash_after=memory_store_hash(mem_after),
            policy_state_hash=policy_state_fingerprint(pol),
            selected_action_id=event.id,
            runtime_ms=runtime_ms,
        )
        self.last_runtime_state = RuntimeState(
            memory_store_hash=memory_store_hash(mem_after),
            policy_state=pol,
            config_hash=self.trace_config_fingerprint,
            last_output_hash=hashlib.sha256(output.encode("utf-8")).hexdigest()[:32],
            global_seed=self._global_seed,
            execution_trace=et,
            output_text=output,
        )
        rec = et.as_dict()
        self.store.append_trace(rec)
        self.last_trace = rec

    def process(self, input: str) -> str:
        if self.shadow_mode:
            snapshot = json.loads(json.dumps(self.store.load_store()))
            prev_backend = self.store.backend
            self.store.backend = InMemoryStorageBackend(snapshot)
            try:
                with model_provider_scope(self.model_provider):
                    return self._process_without_scope(input)
            finally:
                self.store.backend = prev_backend
        with model_provider_scope(self.model_provider):
            return self._process_without_scope(input)

    def _process_without_scope(self, input: str) -> str:
        # Phase 11 unified pipeline stages: load hashes → retrieve → score → select → update → persist
        if self.deterministic_clock:
            self._process_tick += 1
            self.store.set_reference_time(self._deterministic_time_base + self._process_tick)
        else:
            self.store.set_reference_time(None)
        episode_index = len(self.store.read_traces())
        _t_pipeline = time.perf_counter()
        _mem_before = memory_store_hash(self.store.load_store())
        weights_before_decay, weights_after_decay = self.store.decay_weights(
            self.retention_factor,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        events = self.store.events()
        if len(events) > 5000:
            logger.warning(
                "retrieval soft bound exceeded (n=%s events); verify vectorized/cached path",
                len(events),
            )
        clusters = self.store.load_clusters()
        suppressed = self._suppressed_events(input, events)
        candidates = [
            memory
            for memory in self.retrieval.retrieve(input, events, clusters)
            if not self._contains_suppressed_output(memory.event.output, suppressed)
        ]
        strategies = group_by_cluster(candidates, clusters)
        self_model_before = self.store.load_self_model()
        try:
            model_rankings = rank_model_variants(input, strategies, self_model_before)
            model_selected = select_best_model(self_model_before, input, strategies)
        except Exception as exc:  # noqa: BLE001
            logger.exception("model ranking failed: %s", exc)
            self_model_before = self.store.load_self_model()
            model_rankings = {variant: 0.5 for variant in MODEL_VARIANTS}
            model_selected = "baseline"
        strategy_results: List[Dict] = []
        for strategy in strategies:
            try:
                strategy_results.append(
                    simulate_strategy(input, strategy, model_variant=model_selected)
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("simulate_strategy failed: %s", exc)
                strategy_results.append(
                    {
                        "strategy": strategy,
                        "model_variant": model_selected,
                        "predicted_score": 0.0,
                        "uncertainty": 1.0,
                        "counterfactual_sensitivity": 0.0,
                        "counterfactuals": [],
                    }
                )
        best_possible = max(
            [result["predicted_score"] for result in strategy_results],
            default=0.0,
        )
        for result in strategy_results:
            result["regret"] = max(0.0, best_possible - result["predicted_score"])
            adjusted_regret = result["regret"] - float(self_model_before.get("regret_bias", 0.0))
            result["adjusted_regret"] = adjusted_regret
            result["meta_error"] = float(model_rankings.get(model_selected, 0.0))
            result["selection_score"] = result["predicted_score"] - REGRET_WEIGHT * adjusted_regret - 0.1 * result["meta_error"]
            result["strategy"]["regret"] = result["regret"]
            result["strategy"]["counterfactual_risk"] = result["counterfactual_sensitivity"]
            result["strategy"]["stability"] = 1.0 - result["uncertainty"]
            try:
                md = model_dependence(input, result["strategy"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("model_dependence failed: %s", exc)
                md = 1.0
            result["strategy"]["model_dependence"] = md
            result["strategy"]["stability_under_models"] = 1.0 - md
        selected_result = choose_min_regret_strategy(strategy_results)
        selected_strategy = selected_result["strategy"] if selected_result else None
        cluster_variance = _variance([memory.event.expected_value for memory in candidates])
        if self.production_mode:
            exploration_rate = 0.0
            exploration = False
            if self.gated_exploration:
                if self._last_evaluation_confidence < self.gated_exploration_confidence_threshold:
                    exploration_rate = self.gated_exploration_epsilon
                    exploration = self._should_explore_episode(episode_index, exploration_rate)
        else:
            exploration_rate = adaptive_exploration_rate(cluster_variance)
            exploration = self._should_explore_episode(episode_index, exploration_rate)
        selected = self._sample_from_strategy(selected_strategy, candidates)
        if exploration:
            selected = self._diversify_memories(selected, candidates)
        selected = selected[: self.max_memories]

        selected_payloads = [self._retrieval_payload(memory) for memory in selected]
        raw_predicted_score = 0.0
        predicted_score = 0.0
        counterfactual_results: List[Dict] = []
        strategic_output_fallback = False
        if self._circuit.is_open("predict_expected", episode_index):
            raw_predicted_score = heuristic_predict_expected_score(
                input, selected_payloads, model_variant=model_selected
            )
        else:
            try:
                raw_predicted_score = predict_expected_score(
                    input, selected_payloads, model_variant=model_selected
                )
                self._circuit.success("predict_expected")
            except Exception as exc:  # noqa: BLE001
                logger.exception("predict_expected_score failed: %s", exc)
                self._circuit.failure("predict_expected", episode_index)
                raw_predicted_score = heuristic_predict_expected_score(
                    input, selected_payloads, model_variant=model_selected
                )
        predicted_score = _clamp(
            raw_predicted_score - float(self_model_before.get("prediction_bias", 0.0)),
            -1.0,
            1.0,
        )
        try:
            counterfactual_results = [
                counterfactual_evaluate(
                    input, selected_payloads, memory.event.id, model_variant=model_selected
                )
                for memory in selected
            ]
        except Exception as exc:  # noqa: BLE001
            logger.exception("counterfactual_evaluate failed: %s", exc)
            strategic_output_fallback = True
            counterfactual_results = [
                {
                    "removed_memory_id": memory.event.id,
                    "baseline_score": 0.0,
                    "counterfactual_score": 0.0,
                    "delta": 0.0,
                }
                for memory in selected
            ]
        marginal_effects = {
            result["removed_memory_id"]: result["delta"]
            - float(self_model_before.get("causal_attribution_bias", 0.0))
            for result in counterfactual_results
        }
        selected_ids = [memory.event.id for memory in selected]
        diversity_scores = [memory.diversity_factor for memory in selected]
        weights_before_selected = [
            weights_after_decay.get(memory.event.id, memory.event.weight)
            for memory in selected
        ]
        self.store.record_usage(selected_ids)

        context_text, context_memory_ids = self.composer.compose(input, selected)
        policy_before = self.store.load_policy_state()
        if strategic_output_fallback:
            output = fallback_policy_output(
                input, selected, policy_before, predicted_score=predicted_score
            )
        else:
            output = self._generate(input, selected, policy_before, predicted_score)
        eval_ctx = {
            "retrieved": [self._retrieval_payload(memory) for memory in selected],
            "policy_state": policy_before,
            "exploration": exploration,
            "predicted_score": predicted_score,
        }
        if self._circuit.is_open("evaluate", episode_index):
            eval_result = heuristic_evaluate_output(input, output, eval_ctx)
        else:
            try:
                eval_result = evaluate(input, output, eval_ctx)
                self._circuit.success("evaluate")
            except Exception as exc:  # noqa: BLE001
                logger.exception("evaluate failed: %s", exc)
                self._circuit.failure("evaluate", episode_index)
                eval_result = heuristic_evaluate_output(input, output, eval_ctx)
        actual_score = eval_result["score"]
        self._last_evaluation_confidence = float(eval_result.get("confidence", 0.0) or 0.0)
        prediction_error = abs(float(predicted_score) - float(actual_score))

        event = self.store.create_event(input, output, event_type="interaction")
        weight_before, weight_after = self.store.update_weight(
            event.id,
            actual_score,
            retention_factor=self.retention_factor,
            learning_rate=self.learning_rate,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        attribution_updates = self.store.update_counterfactual_attribution(
            marginal_effects,
            actual_score,
            gamma=self.gamma,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        temporal_updates = self.store.apply_temporal_credit(
            actual_score,
            event.id,
            input,
            window_size=self.temporal_window,
            gamma=self.gamma,
            marginal_effects=marginal_effects,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        refreshed_event = self.store.events()[-1]
        policy_snapshot = self.store.update_policy_state(
            eval_result,
            refreshed_event,
            selected,
            exploration_rate=exploration_rate,
            prediction_error=prediction_error,
        )
        state_after = self.store.load_state()
        strategy_scores = [result["predicted_score"] for result in strategy_results]
        regret_values = [result["regret"] for result in strategy_results]
        counterfactual_deltas = [result["delta"] for result in counterfactual_results]
        selected_gap = 0.0
        if selected_result:
            selected_gap = best_possible - selected_result["predicted_score"]
        policy_stability = self.store.policy_stability()
        sensitivity_map = {
            event_id: {
                "marginal_effect": payload.get("marginal_effect", 0.0),
                "sensitivity_score": payload.get("sensitivity_score", 0.0),
            }
            for event_id, payload in sorted(state_after.items())
        }
        selected_prediction = {
            "predicted_score": predicted_score,
            "raw_predicted_score": raw_predicted_score,
            "regret": selected_result["regret"] if selected_result else 0.0,
            "selected_vs_best_gap": selected_gap,
            "confidence": eval_result["confidence"],
            "marginal_effects": marginal_effects,
        }
        meta_result = evaluate_model_quality(
            selected_prediction,
            {
                "score": actual_score,
                "confidence": eval_result["confidence"],
                "marginal_effects": marginal_effects,
            },
            {
                "best_possible": best_possible,
                "strategy_results": strategy_results,
                "attribution_updates": attribution_updates,
            },
        )
        self_model_snapshot = self.store.update_self_model(meta_result, model_selected, eval_result["confidence"])
        self._finalize_execution(
            input=input,
            output=output,
            event=event,
            memory_hash_before=_mem_before,
            t_pipeline=_t_pipeline,
        )
        self.last_event_id = event.id
        return output

    async def aprocess(self, input: str) -> str:
        if self.shadow_mode:
            snapshot = json.loads(json.dumps(self.store.load_store()))
            prev_backend = self.store.backend
            self.store.backend = InMemoryStorageBackend(snapshot)
            try:
                async with async_model_provider_scope(self.model_provider):
                    return await self._aprocess_without_scope(input)
            finally:
                self.store.backend = prev_backend
        async with async_model_provider_scope(self.model_provider):
            return await self._aprocess_without_scope(input)

    async def _aprocess_without_scope(self, input: str) -> str:
        if self.deterministic_clock:
            self._process_tick += 1
            self.store.set_reference_time(self._deterministic_time_base + self._process_tick)
        else:
            self.store.set_reference_time(None)
        episode_index = len(self.store.read_traces())
        _t_pipeline = time.perf_counter()
        _mem_before = memory_store_hash(self.store.load_store())
        weights_before_decay, weights_after_decay = self.store.decay_weights(
            self.retention_factor,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        events = self.store.events()
        if len(events) > 5000:
            logger.warning(
                "retrieval soft bound exceeded (n=%s events); verify vectorized/cached path",
                len(events),
            )
        clusters = self.store.load_clusters()
        suppressed = self._suppressed_events(input, events)
        candidates = [
            memory
            for memory in self.retrieval.retrieve(input, events, clusters)
            if not self._contains_suppressed_output(memory.event.output, suppressed)
        ]
        strategies = group_by_cluster(candidates, clusters)
        self_model_before = self.store.load_self_model()
        try:
            model_rankings = await arank_model_variants(input, strategies, self_model_before)
            model_selected = await aselect_best_model(self_model_before, input, strategies)
        except Exception as exc:  # noqa: BLE001
            logger.exception("model ranking failed: %s", exc)
            self_model_before = self.store.load_self_model()
            model_rankings = {variant: 0.5 for variant in MODEL_VARIANTS}
            model_selected = "baseline"
        strategy_results: List[Dict] = []
        for strategy in strategies:
            try:
                strategy_results.append(
                    await asimulate_strategy(input, strategy, model_variant=model_selected)
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("simulate_strategy failed: %s", exc)
                strategy_results.append(
                    {
                        "strategy": strategy,
                        "model_variant": model_selected,
                        "predicted_score": 0.0,
                        "uncertainty": 1.0,
                        "counterfactual_sensitivity": 0.0,
                        "counterfactuals": [],
                    }
                )
        best_possible = max(
            [result["predicted_score"] for result in strategy_results],
            default=0.0,
        )
        for result in strategy_results:
            result["regret"] = max(0.0, best_possible - result["predicted_score"])
            adjusted_regret = result["regret"] - float(self_model_before.get("regret_bias", 0.0))
            result["adjusted_regret"] = adjusted_regret
            result["meta_error"] = float(model_rankings.get(model_selected, 0.0))
            result["selection_score"] = result["predicted_score"] - REGRET_WEIGHT * adjusted_regret - 0.1 * result["meta_error"]
            result["strategy"]["regret"] = result["regret"]
            result["strategy"]["counterfactual_risk"] = result["counterfactual_sensitivity"]
            result["strategy"]["stability"] = 1.0 - result["uncertainty"]
            try:
                md = await amodel_dependence(input, result["strategy"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("model_dependence failed: %s", exc)
                md = 1.0
            result["strategy"]["model_dependence"] = md
            result["strategy"]["stability_under_models"] = 1.0 - md
        selected_result = choose_min_regret_strategy(strategy_results)
        selected_strategy = selected_result["strategy"] if selected_result else None
        cluster_variance = _variance([memory.event.expected_value for memory in candidates])
        if self.production_mode:
            exploration_rate = 0.0
            exploration = False
            if self.gated_exploration:
                if self._last_evaluation_confidence < self.gated_exploration_confidence_threshold:
                    exploration_rate = self.gated_exploration_epsilon
                    exploration = self._should_explore_episode(episode_index, exploration_rate)
        else:
            exploration_rate = adaptive_exploration_rate(cluster_variance)
            exploration = self._should_explore_episode(episode_index, exploration_rate)
        selected = self._sample_from_strategy(selected_strategy, candidates)
        if exploration:
            selected = self._diversify_memories(selected, candidates)
        selected = selected[: self.max_memories]

        selected_payloads = [self._retrieval_payload(memory) for memory in selected]
        raw_predicted_score = 0.0
        predicted_score = 0.0
        counterfactual_results: List[Dict] = []
        strategic_output_fallback = False
        if self._circuit.is_open("predict_expected", episode_index):
            raw_predicted_score = heuristic_predict_expected_score(
                input, selected_payloads, model_variant=model_selected
            )
        else:
            try:
                raw_predicted_score = await apredict_expected_score(
                    input, selected_payloads, model_variant=model_selected
                )
                self._circuit.success("predict_expected")
            except Exception as exc:  # noqa: BLE001
                logger.exception("predict_expected_score failed: %s", exc)
                self._circuit.failure("predict_expected", episode_index)
                raw_predicted_score = heuristic_predict_expected_score(
                    input, selected_payloads, model_variant=model_selected
                )
        predicted_score = _clamp(
            raw_predicted_score - float(self_model_before.get("prediction_bias", 0.0)),
            -1.0,
            1.0,
        )
        try:
            counterfactual_results = []
            for memory in selected:
                counterfactual_results.append(
                    await acounterfactual_evaluate(
                        input, selected_payloads, memory.event.id, model_variant=model_selected
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("counterfactual_evaluate failed: %s", exc)
            strategic_output_fallback = True
            counterfactual_results = [
                {
                    "removed_memory_id": memory.event.id,
                    "baseline_score": 0.0,
                    "counterfactual_score": 0.0,
                    "delta": 0.0,
                }
                for memory in selected
            ]
        marginal_effects = {
            result["removed_memory_id"]: result["delta"]
            - float(self_model_before.get("causal_attribution_bias", 0.0))
            for result in counterfactual_results
        }
        selected_ids = [memory.event.id for memory in selected]
        diversity_scores = [memory.diversity_factor for memory in selected]
        weights_before_selected = [
            weights_after_decay.get(memory.event.id, memory.event.weight)
            for memory in selected
        ]
        self.store.record_usage(selected_ids)

        context_text, context_memory_ids = self.composer.compose(input, selected)
        policy_before = self.store.load_policy_state()
        if strategic_output_fallback:
            output = fallback_policy_output(
                input, selected, policy_before, predicted_score=predicted_score
            )
        else:
            output = self._generate(input, selected, policy_before, predicted_score)
        eval_ctx = {
            "retrieved": [self._retrieval_payload(memory) for memory in selected],
            "policy_state": policy_before,
            "exploration": exploration,
            "predicted_score": predicted_score,
        }
        if self._circuit.is_open("evaluate", episode_index):
            eval_result = heuristic_evaluate_output(input, output, eval_ctx)
        else:
            try:
                eval_result = await aevaluate(input, output, eval_ctx)
                self._circuit.success("evaluate")
            except Exception as exc:  # noqa: BLE001
                logger.exception("evaluate failed: %s", exc)
                self._circuit.failure("evaluate", episode_index)
                eval_result = heuristic_evaluate_output(input, output, eval_ctx)
        actual_score = eval_result["score"]
        self._last_evaluation_confidence = float(eval_result.get("confidence", 0.0) or 0.0)
        prediction_error = abs(float(predicted_score) - float(actual_score))

        event = self.store.create_event(input, output, event_type="interaction")
        weight_before, weight_after = self.store.update_weight(
            event.id,
            actual_score,
            retention_factor=self.retention_factor,
            learning_rate=self.learning_rate,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        attribution_updates = self.store.update_counterfactual_attribution(
            marginal_effects,
            actual_score,
            gamma=self.gamma,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        temporal_updates = self.store.apply_temporal_credit(
            actual_score,
            event.id,
            input,
            window_size=self.temporal_window,
            gamma=self.gamma,
            marginal_effects=marginal_effects,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        refreshed_event = self.store.events()[-1]
        policy_snapshot = self.store.update_policy_state(
            eval_result,
            refreshed_event,
            selected,
            exploration_rate=exploration_rate,
            prediction_error=prediction_error,
        )
        state_after = self.store.load_state()
        strategy_scores = [result["predicted_score"] for result in strategy_results]
        regret_values = [result["regret"] for result in strategy_results]
        counterfactual_deltas = [result["delta"] for result in counterfactual_results]
        selected_gap = 0.0
        if selected_result:
            selected_gap = best_possible - selected_result["predicted_score"]
        policy_stability = self.store.policy_stability()
        sensitivity_map = {
            event_id: {
                "marginal_effect": payload.get("marginal_effect", 0.0),
                "sensitivity_score": payload.get("sensitivity_score", 0.0),
            }
            for event_id, payload in sorted(state_after.items())
        }
        selected_prediction = {
            "predicted_score": predicted_score,
            "raw_predicted_score": raw_predicted_score,
            "regret": selected_result["regret"] if selected_result else 0.0,
            "selected_vs_best_gap": selected_gap,
            "confidence": eval_result["confidence"],
            "marginal_effects": marginal_effects,
        }
        meta_result = evaluate_model_quality(
            selected_prediction,
            {
                "score": actual_score,
                "confidence": eval_result["confidence"],
                "marginal_effects": marginal_effects,
            },
            {
                "best_possible": best_possible,
                "strategy_results": strategy_results,
                "attribution_updates": attribution_updates,
            },
        )
        self_model_snapshot = self.store.update_self_model(meta_result, model_selected, eval_result["confidence"])
        self._finalize_execution(
            input=input,
            output=output,
            event=event,
            memory_hash_before=_mem_before,
            t_pipeline=_t_pipeline,
        )
        self.last_event_id = event.id
        return output


    def feedback(self, event_id: str, signal: float) -> Tuple[float, float]:
        before, after = self.store.update_weight(
            event_id,
            signal,
            retention_factor=self.retention_factor,
            learning_rate=self.learning_rate,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        self.last_trace = {
            "feedback_event_id": event_id,
            "signal": _clamp_signal(signal),
            "weight_before": before,
            "weight_after": after,
            "timestamp": self.store._eval_now(),
        }
        return before, after

    def retrieve(self, input: str, k: Optional[int] = None) -> List[MemoryEvent]:
        engine = self.retrieval if k is None else RetrievalEngine(k, self.threshold)
        return [memory.event for memory in engine.retrieve(input, self.store.events(), self.store.load_clusters())][:k]

    def retrieval_set(self, input: str) -> List[Dict]:
        return [
            self._retrieval_payload(memory)
            for memory in self.retrieval.retrieve(input, self.store.events(), self.store.load_clusters())
        ]

    def inspect_log(self) -> List[Dict]:
        return self.store.read_log()

    def inspect_traces(self) -> List[Dict]:
        return self.store.read_traces()

    def evaluate(self, input: str, output: str, context: Optional[Dict] = None) -> Dict:
        return evaluate(input, output, context or {})

    def _should_explore_episode(self, episode_index: int, exploration_rate: float) -> bool:
        """Stochastic tie-break derives only from ``GLOBAL_SEED`` (config+agent) and step ordinal."""

        value = _deterministic_unit(f"{self._global_seed}:{episode_index}")
        return value < exploration_rate

    def _sample_from_strategy(self, strategy: Optional[Dict], candidates: Sequence[RetrievedMemory]) -> List[RetrievedMemory]:
        if not strategy:
            return list(candidates[: self.max_memories])
        cluster_id = strategy.get("cluster_id", "")
        strategy_memories = [memory for memory in candidates if memory.event.cluster_id == cluster_id]
        return strategy_memories[: self.max_memories]

    def _diversify_memories(
        self,
        selected: Sequence[RetrievedMemory],
        candidates: Sequence[RetrievedMemory],
    ) -> List[RetrievedMemory]:
        selected_ids = {memory.event.id for memory in selected}
        diversified = list(selected)
        for candidate in candidates:
            if candidate.event.id in selected_ids:
                continue
            if candidate.event.cluster_id not in {memory.event.cluster_id for memory in diversified}:
                diversified.append(candidate)
                selected_ids.add(candidate.event.id)
            if len(diversified) >= self.max_memories:
                break
        if len(diversified) < self.max_memories:
            for candidate in candidates:
                if candidate.event.id not in selected_ids:
                    diversified.append(candidate)
                    selected_ids.add(candidate.event.id)
                if len(diversified) >= self.max_memories:
                    break
        return diversified[: self.max_memories]

    def _generate(
        self,
        input: str,
        selected: Sequence[RetrievedMemory],
        policy_state: Dict,
        predicted_score: float,
    ) -> str:
        task = _normalized_task(input)
        preferred = policy_state.get("preferred_patterns", {})
        preferred_terms = sorted(preferred, key=lambda term: (-float(preferred[term]), term))[:3]
        preferred_text = ", ".join(preferred_terms) if preferred_terms else "clear"

        if not selected:
            return (
                f"Task: {task}\n"
                "Answer: clear baseline response.\n"
                "Plan: identify context, produce one useful step."
            )

        dominant = _strip_generated_sections(selected[0].event.output)
        if len(selected) >= 2 or "sequence" in _concept_terms(input) or predicted_score > 0.55:
            return (
                f"Task: {task}\n"
                "Answer: concise, structured, specific, actionable.\n"
                "Steps: 1. assess context 2. apply predicted strategy 3. verify outcome.\n"
                f"Policy: prefer {preferred_text}.\n"
                f"Pattern: {dominant}"
            )
        return (
            f"Task: {task}\n"
            "Answer: clear, structured, specific.\n"
            f"Policy: prefer {preferred_text}.\n"
            f"Pattern: {dominant}"
        )

    def _suppressed_events(self, input: str, events: Sequence[MemoryEvent]) -> List[MemoryEvent]:
        query_embedding = embed(input)
        suppressed: List[Tuple[float, MemoryEvent]] = []
        for event in events:
            if event.weight >= self.threshold or event.type == "system":
                continue
            sim = cosine_similarity(query_embedding, event.embedding)
            if sim <= 0.0:
                continue
            suppressed.append((sim, event))
        suppressed.sort(key=lambda item: (-item[0], item[1].timestamp, item[1].id))
        return [event for _, event in suppressed]

    def _contains_suppressed_output(self, output: str, suppressed: Sequence[MemoryEvent]) -> bool:
        stripped = _strip_generated_sections(output)
        return any(event.output and _strip_generated_sections(event.output) in stripped for event in suppressed)

    def _retrieval_payload(self, memory: RetrievedMemory) -> Dict:
        event = memory.event
        return {
            "id": event.id,
            "agent_id": event.agent_id,
            "input": event.input,
            "output": event.output,
            "outcome_signal": event.outcome_signal,
            "weight": event.weight,
            "timestamp": event.timestamp,
            "type": event.type,
            "future_score": event.future_score,
            "usage_count": event.usage_count,
            "cluster_id": event.cluster_id,
            "expected_value": event.expected_value,
            "variance": event.variance,
            "recent_scores": event.recent_scores,
            "similarity": memory.similarity,
            "score": memory.score,
            "diversity_factor": memory.diversity_factor,
            "cluster_weight": memory.cluster_weight,
        }


def heuristic_predict_expected_score(
    input: str,
    candidate_memories: Sequence[Dict],
    *,
    model_variant: str = "baseline",
) -> float:
    values: List[float] = []
    similarity_multiplier = _model_similarity_multiplier(model_variant)
    reward_multiplier = _model_reward_multiplier(model_variant)
    for memory in candidate_memories:
        future_score = float(memory.get("future_score", memory.get("expected_value", 0.0)) or 0.0)
        if future_score == 0.0:
            future_score = float(memory.get("expected_value", 0.0) or 0.0)
        sim = _clamp(similarity(input, str(memory.get("input", ""))) * similarity_multiplier, 0.0, 1.0)
        values.append(sim * float(memory.get("weight", 0.0)) * future_score * reward_multiplier)
    return _clamp(_mean(values), -1.0, 1.0)


def predict_expected_score(
    input: str,
    candidate_memories: Sequence[Dict],
    *,
    model_variant: str = "baseline",
) -> float:
    provider = get_active_model_provider()
    if provider is None:
        return heuristic_predict_expected_score(
            input, candidate_memories, model_variant=model_variant
        )
    return provider.predict_expected_score(
        input, candidate_memories, model_variant=model_variant
    )


def adaptive_exploration_rate(cluster_variance: float) -> float:
    return _clamp(cluster_variance * EXPLORATION_VARIANCE_SCALE, EXPLORATION_MIN, EXPLORATION_MAX)


def group_by_cluster(candidates: Sequence[RetrievedMemory], clusters: Dict[str, Dict]) -> List[Dict]:
    grouped: Dict[str, List[RetrievedMemory]] = {}
    for memory in candidates:
        grouped.setdefault(memory.event.cluster_id or "cluster_unassigned", []).append(memory)

    strategies: List[Dict] = []
    for cluster_id, memories in sorted(grouped.items(), key=lambda item: item[0]):
        expected_values = [memory.event.expected_value for memory in memories]
        cluster = clusters.get(cluster_id, {})
        expected_value = max(_mean(expected_values), float(cluster.get("expected_value", 0.0)))
        memory_payloads = [
            {
                "id": memory.event.id,
                "input": memory.event.input,
                "output": memory.event.output,
                "weight": memory.event.weight,
                "future_score": memory.event.future_score,
                "expected_value": memory.event.expected_value,
                "similarity": memory.similarity,
            }
            for memory in memories
        ]
        strategy = {
            "cluster_id": cluster_id,
            "expected_value": expected_value,
            "variance": _variance(expected_values),
            "usage_count": int(cluster.get("usage_count", sum(memory.event.usage_count for memory in memories))),
            "dominant_memories": [memory.event.id for memory in memories[:3]],
            "score": expected_value * _mean([memory.similarity for memory in memories]) if memories else 0.0,
            "memories": memory_payloads,
            "prediction_error_history": [],
            "regret_error_history": [],
            "model_dependence": 0.0,
            "stability_under_models": 1.0,
        }
        strategies.append(strategy)
    strategies.sort(key=lambda item: (-item["expected_value"], item["usage_count"], item["cluster_id"]))
    return strategies


def counterfactual_evaluate(
    input: str,
    memories: Sequence[Dict],
    removed_memory_id: str,
    *,
    model_variant: str = "baseline",
) -> Dict:
    baseline_score = predict_expected_score(input, memories, model_variant=model_variant)
    remaining = [memory for memory in memories if memory.get("id") != removed_memory_id]
    counterfactual_score = predict_expected_score(input, remaining, model_variant=model_variant)
    return {
        "removed_memory_id": removed_memory_id,
        "baseline_score": baseline_score,
        "counterfactual_score": counterfactual_score,
        "delta": baseline_score - counterfactual_score,
    }


def simulate_strategy(
    input: str,
    strategy: Dict,
    candidates: Optional[Sequence[RetrievedMemory]] = None,
    *,
    model_variant: str = "baseline",
) -> Dict:
    memories = list(strategy.get("memories", []))
    predicted_score = predict_expected_score(input, memories, model_variant=model_variant)
    counterfactuals = [
        counterfactual_evaluate(input, memories, memory.get("id", ""), model_variant=model_variant)
        for memory in memories
    ]
    deltas = [abs(result["delta"]) for result in counterfactuals]
    uncertainty = float(strategy.get("variance", _variance([memory.get("expected_value", 0.0) for memory in memories])))
    return {
        "strategy": strategy,
        "model_variant": model_variant,
        "predicted_score": predicted_score,
        "uncertainty": uncertainty,
        "counterfactual_sensitivity": _mean(deltas),
        "counterfactuals": counterfactuals,
    }


def counterfactual_model_variation(test_input: str, model_variant: str, strategy: Optional[Dict] = None) -> float:
    strategy = strategy or {"memories": []}
    return simulate_strategy(test_input, strategy, model_variant=model_variant)["predicted_score"]


def evaluate_model_quality(prediction: Dict, actual: Optional[Dict], context: Dict) -> Dict:
    actual = actual or {}
    predicted_score = float(prediction.get("predicted_score", 0.0))
    actual_score = float(actual.get("score", predicted_score))
    signed_prediction_error = predicted_score - actual_score
    prediction_error = abs(signed_prediction_error)
    best_possible = float(context.get("best_possible", predicted_score))
    selected_gap = float(prediction.get("selected_vs_best_gap", 0.0))
    regret_error = abs(selected_gap - max(0.0, best_possible - predicted_score))
    meff = prediction.get("marginal_effects", {}) or {}
    marginal_effects = [abs(float(meff[k])) for k in sorted(meff)]
    attribution_updates = context.get("attribution_updates", [])
    update_effects = [abs(float(update.get("marginal_effect", 0.0))) for update in attribution_updates]
    attribution_error = abs(_mean(marginal_effects) - _mean(update_effects))
    overall = _clamp(
        prediction_error * 0.5 + regret_error * 0.25 + attribution_error * 0.25,
        0.0,
        1.0,
    )
    return {
        "prediction_error": prediction_error,
        "signed_prediction_error": signed_prediction_error,
        "regret_error": regret_error,
        "attribution_error": attribution_error,
        "overall_meta_score": overall,
    }


def rank_model_variants(input: str, strategies: Sequence[Dict], self_model: Dict) -> Dict[str, float]:
    rankings = self_model.get("model_rankings", {})
    result: Dict[str, float] = {}
    reference = strategies[0] if strategies else {"memories": []}
    baseline = counterfactual_model_variation(input, "baseline", reference)
    for variant in MODEL_VARIANTS:
        predicted = counterfactual_model_variation(input, variant, reference)
        historical = float(rankings.get(variant, 0.5))
        variation_penalty = abs(predicted - baseline)
        bias_penalty = abs(float(self_model.get("prediction_bias", 0.0)))
        result[variant] = _clamp(historical + variation_penalty + bias_penalty, 0.0, 1.0)
    return result


def select_best_model(self_model: Dict, input: str, strategies: Sequence[Dict]) -> str:
    rankings = rank_model_variants(input, strategies, self_model)
    if not rankings:
        return "baseline"
    return min(rankings, key=lambda variant: (rankings[variant], variant))


def model_dependence(input: str, strategy: Dict) -> float:
    scores = [counterfactual_model_variation(input, variant, strategy) for variant in MODEL_VARIANTS]
    return _variance(scores)


def _model_similarity_multiplier(model_variant: str) -> float:
    if model_variant == "low_bias":
        return 0.95
    if model_variant == "high_sensitivity":
        return 1.08
    return 1.0


def _model_reward_multiplier(model_variant: str) -> float:
    if model_variant == "delayed_reward":
        return 1.1
    if model_variant == "high_sensitivity":
        return 1.05
    return 1.0


def compute_regret(predicted_score: float, best_possible_score: float) -> float:
    return max(0.0, best_possible_score - predicted_score)


def choose_lowest_regret_strategy(
    strategy_results: Sequence[Dict],
    *,
    regret_weight: float = 0.35,
) -> Optional[Dict]:
    if not strategy_results:
        return None
    return max(
        strategy_results,
        key=lambda result: (
            result["predicted_score"] - regret_weight * result["regret"],
            -result["regret"],
            result["strategy"].get("cluster_id", ""),
        ),
    )


def choose_min_regret_strategy(
    strategy_results: Sequence[Dict],
    *,
    regret_weight: float = 0.35,
) -> Optional[Dict]:
    return choose_lowest_regret_strategy(strategy_results, regret_weight=regret_weight)


def choose_max_expected_value(strategies: Sequence[Dict]) -> Optional[Dict]:
    if not strategies:
        return None
    return max(strategies, key=lambda item: (item["expected_value"], -item["usage_count"], item["cluster_id"]))


def heuristic_evaluate_output(input: str, output: str, context: Optional[Dict] = None) -> Dict:
    """Composite deterministic evaluator returning score/components/confidence."""

    context = context or {}
    output_tokens = _tokens(output)
    unique_tokens = set(output_tokens)
    relevance = cosine_similarity(embed(input), embed(output))
    if "Task:" in output:
        relevance = max(relevance, 0.72)
    relevance = _clamp(relevance, 0.0, 1.0)

    repetition = _repetition_ratio(output_tokens)
    structure_bonus = 0.25 if "Answer:" in output else 0.0
    structure_bonus += 0.15 if "Steps:" in output or "Plan:" in output else 0.0
    coherence = _clamp(0.65 + structure_bonus - repetition * 0.45, 0.0, 1.0)

    success_hits = len(unique_tokens & _SUCCESS_TERMS)
    failure_hits = len(unique_tokens & _FAILURE_TERMS)
    usefulness = 0.35 + min(0.45, success_hits * 0.09)
    if "Pattern:" in output:
        usefulness += 0.12
    if "verify" in unique_tokens or "outcome" in unique_tokens:
        usefulness += 0.08
    usefulness -= min(0.5, failure_hits * 0.16)
    usefulness = _clamp(usefulness, 0.0, 1.0)

    confidence = 0.55
    if len(output_tokens) >= max(8, len(_tokens(input))):
        confidence += 0.15
    if "Answer:" in output:
        confidence += 0.1
    if context.get("retrieved"):
        confidence += min(0.15, 0.05 * len(context["retrieved"]))
    if context.get("exploration"):
        confidence -= 0.05
    predicted_score = context.get("predicted_score")
    if predicted_score is not None:
        confidence += max(0.0, 0.05 - abs(float(predicted_score)) * 0.02)
    confidence = _clamp(confidence, 0.0, 1.0)

    weighted = relevance * 0.35 + coherence * 0.3 + usefulness * 0.35
    score = _clamp((weighted * 2.0 - 1.0) * confidence, -1.0, 1.0)
    return {
        "score": score,
        "components": {
            "relevance": relevance,
            "coherence": coherence,
            "usefulness": usefulness,
        },
        "confidence": confidence,
    }


def evaluate(input: str, output: str, context: Optional[Dict] = None) -> Dict:
    """Delegate to ``ModelProvider`` when active, else heuristic rules."""

    provider = get_active_model_provider()
    if provider is None:
        return heuristic_evaluate_output(input, output, context)
    return provider.evaluate(input, output, context)


async def apredict_expected_score(
    input: str,
    candidate_memories: Sequence[Dict],
    *,
    model_variant: str = "baseline",
) -> float:
    provider = get_active_model_provider()
    if provider is None:
        return heuristic_predict_expected_score(
            input, candidate_memories, model_variant=model_variant
        )
    return await provider.apredict_expected_score(
        input, candidate_memories, model_variant=model_variant
    )


async def aevaluate(input: str, output: str, context: Optional[Dict] = None) -> Dict:
    provider = get_active_model_provider()
    if provider is None:
        return heuristic_evaluate_output(input, output, context)
    return await provider.aevaluate(input, output, context)


async def acounterfactual_evaluate(
    input: str,
    memories: Sequence[Dict],
    removed_memory_id: str,
    *,
    model_variant: str = "baseline",
) -> Dict:
    baseline_score = await apredict_expected_score(input, memories, model_variant=model_variant)
    remaining = [memory for memory in memories if memory.get("id") != removed_memory_id]
    counterfactual_score = await apredict_expected_score(input, remaining, model_variant=model_variant)
    return {
        "removed_memory_id": removed_memory_id,
        "baseline_score": baseline_score,
        "counterfactual_score": counterfactual_score,
        "delta": baseline_score - counterfactual_score,
    }


async def asimulate_strategy(
    input: str,
    strategy: Dict,
    candidates: Optional[Sequence[RetrievedMemory]] = None,
    *,
    model_variant: str = "baseline",
) -> Dict:
    memories = list(strategy.get("memories", []))
    predicted_score = await apredict_expected_score(input, memories, model_variant=model_variant)
    counterfactuals: List[Dict] = []
    for memory in memories:
        counterfactuals.append(
            await acounterfactual_evaluate(
                input, memories, memory.get("id", ""), model_variant=model_variant
            )
        )
    deltas = [abs(result["delta"]) for result in counterfactuals]
    uncertainty = float(
        strategy.get("variance", _variance([memory.get("expected_value", 0.0) for memory in memories]))
    )
    return {
        "strategy": strategy,
        "model_variant": model_variant,
        "predicted_score": predicted_score,
        "uncertainty": uncertainty,
        "counterfactual_sensitivity": _mean(deltas),
        "counterfactuals": counterfactuals,
    }


async def acounterfactual_model_variation(
    test_input: str, model_variant: str, strategy: Optional[Dict] = None
) -> float:
    strategy = strategy or {"memories": []}
    return (await asimulate_strategy(test_input, strategy, model_variant=model_variant))["predicted_score"]


async def arank_model_variants(input: str, strategies: Sequence[Dict], self_model: Dict) -> Dict[str, float]:
    rankings = self_model.get("model_rankings", {})
    result: Dict[str, float] = {}
    reference = strategies[0] if strategies else {"memories": []}
    baseline = await acounterfactual_model_variation(input, "baseline", reference)
    for variant in MODEL_VARIANTS:
        predicted = await acounterfactual_model_variation(input, variant, reference)
        historical = float(rankings.get(variant, 0.5))
        variation_penalty = abs(predicted - baseline)
        bias_penalty = abs(float(self_model.get("prediction_bias", 0.0)))
        result[variant] = _clamp(historical + variation_penalty + bias_penalty, 0.0, 1.0)
    return result


async def aselect_best_model(self_model: Dict, input: str, strategies: Sequence[Dict]) -> str:
    rankings = await arank_model_variants(input, strategies, self_model)
    if not rankings:
        return "baseline"
    return min(rankings, key=lambda variant: (rankings[variant], variant))


async def amodel_dependence(input: str, strategy: Dict) -> float:
    scores = [await acounterfactual_model_variation(input, variant, strategy) for variant in MODEL_VARIANTS]
    return _variance(scores)


def _average_embedding(embeddings: Sequence[Dict[str, float]]) -> Dict[str, float]:
    vectors = [embedding for embedding in embeddings if embedding]
    if not vectors:
        return {}
    totals: Dict[str, float] = {}
    for vector in vectors:
        for term, value in vector.items():
            totals[term] = totals.get(term, 0.0) + value
    count = float(len(vectors))
    return {term: value / count for term, value in sorted(totals.items())}


def _deterministic_unit(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12 - 1)


def _pattern_terms(output: str) -> List[str]:
    terms = []
    for term in _concept_terms(output):
        if term in _SUCCESS_TERMS or term in {"policy", "sequence", "structured"}:
            terms.append(term)
    return sorted(set(terms))


def _repetition_ratio(tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    return 1.0 - (len(set(tokens)) / len(tokens))


def _normalized_task(input: str) -> str:
    tokens = _tokens(input)
    return " ".join(tokens) if tokens else "empty input"


def _strip_generated_sections(output: str) -> str:
    lines = [
        line
        for line in output.splitlines()
        if not line.startswith("Task:") and not line.startswith("Pattern:")
    ]
    return " ".join(line.strip() for line in lines if line.strip())


_DEFAULT_LOOP: Optional[ResonanceWeightedMemoryGraph] = None


def default_loop() -> ResonanceWeightedMemoryGraph:
    global _DEFAULT_LOOP
    if _DEFAULT_LOOP is None:
        _DEFAULT_LOOP = ResonanceWeightedMemoryGraph()
    return _DEFAULT_LOOP


def process(input: str) -> str:
    return default_loop().process(input)


def feedback(event_id: str, signal: float) -> Tuple[float, float]:
    return default_loop().feedback(event_id, signal)


__all__ = [
    "EMBEDDING_VERSION",
    "SCHEMA_VERSION",
    "ContextComposer",
    "MemoryEvent",
    "MemoryStore",
    "ResonanceWeightedMemoryGraph",
    "RetrievalEngine",
    "RetrievedMemory",
    "adaptive_exploration_rate",
    "choose_max_expected_value",
    "choose_min_regret_strategy",
    "compute_regret",
    "counterfactual_evaluate",
    "cosine_similarity",
    "embed",
    "evaluate",
    "fallback_policy_output",
    "feedback",
    "memory_store_hash",
    "group_by_cluster",
    "heuristic_evaluate_output",
    "heuristic_predict_expected_score",
    "predict_expected_score",
    "process",
    "simulate_strategy",
    "similarity",
    "validate_schema",
]
