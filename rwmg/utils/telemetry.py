"""Centralized telemetry export (JSON / Prometheus text)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TelemetrySnapshot:
    prediction_error: float
    regret_magnitude: float
    policy_stability_index: float
    latency_p99: float


class TelemetryProbe:
    """Aggregates per-cycle signals aligned with policy traces and policy_state."""

    def __init__(self) -> None:
        self._latencies: List[float] = []
        self._regret_mags: List[float] = []
        self._stability: float = 0.0
        self._last_policy: Dict[str, Any] = {}

    def observe_cycle(
        self,
        *,
        trace: Optional[Dict[str, Any]],
        policy_state: Dict[str, Any],
        latency_s: float,
    ) -> None:
        self._latencies.append(float(latency_s))
        self._last_policy = dict(policy_state)
        if trace:
            rvals = trace.get("regret_values") or []
            if rvals:
                self._regret_mags.append(max(float(x) for x in rvals))
            else:
                self._regret_mags.append(float(trace.get("regret_error", 0.0) or 0.0))
            self._stability = float(trace.get("policy_stability", 0.0) or 0.0)

    @staticmethod
    def _p99(values: List[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(len(s) - 1, max(0, int(math.ceil(0.99 * len(s))) - 1))
        return float(s[idx])

    def snapshot(self) -> TelemetrySnapshot:
        pe = float(self._last_policy.get("prediction_error_moving_avg", 0.0))
        rm = float(self._regret_mags[-1]) if self._regret_mags else 0.0
        return TelemetrySnapshot(
            prediction_error=pe,
            regret_magnitude=rm,
            policy_stability_index=float(self._stability),
            latency_p99=self._p99(self._latencies),
        )

    def to_json(self) -> Dict[str, Any]:
        sn = self.snapshot()
        return {
            "prediction_error": round(sn.prediction_error, 6),
            "regret_magnitude": round(sn.regret_magnitude, 6),
            "policy_stability_index": round(sn.policy_stability_index, 6),
            "latency_p99": round(sn.latency_p99, 8),
        }

    def to_prometheus(self) -> str:
        m = self.to_json()
        lines = [
            "# HELP rwmg_prediction_error_moving_avg Smoothed |predicted-actual| from policy_state.",
            "# TYPE rwmg_prediction_error_moving_avg gauge",
            f"rwmg_prediction_error_moving_avg {m['prediction_error']}",
            "# HELP rwmg_regret_magnitude Max regret from last observed trace.",
            "# TYPE rwmg_regret_magnitude gauge",
            f"rwmg_regret_magnitude {m['regret_magnitude']}",
            "# HELP rwmg_policy_stability_index Policy stability from last trace.",
            "# TYPE rwmg_policy_stability_index gauge",
            f"rwmg_policy_stability_index {m['policy_stability_index']}",
            "# HELP rwmg_latency_p99_seconds p99 process latency in seconds.",
            "# TYPE rwmg_latency_p99_seconds gauge",
            f"rwmg_latency_p99_seconds {m['latency_p99']}",
        ]
        return "\n".join(lines) + "\n"


__all__ = ["TelemetryProbe", "TelemetrySnapshot"]
