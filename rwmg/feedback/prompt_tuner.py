"""Adaptive prompt tuning based on historical resonance feedback."""
from __future__ import annotations

from typing import Dict, List


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    """Clamp ``value`` to the inclusive range ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def tune_prompt_parameters(agent_id: str, memory_log: List[Dict], current_state: Dict) -> Dict:
    """Adjust an agent's state according to recent resonance feedback.

    Parameters
    ----------
    agent_id:
        Identifier of the agent being tuned.  Currently unused but retained for
        future extensibility.
    memory_log:
        Parsed contents of ``memory_log.json`` for the agent.  The function
        expects an iterable of memory dictionaries where feedback entries contain
        at least ``"type"`` and ``"resonance_score"`` fields and optionally
        ``"tone"`` or ``"topic"`` hints.
    current_state:
        The agent's current state as loaded from ``agent_state.json``.

    Returns
    -------
    Dict
        A modified copy of ``current_state`` with updated emotional vectors,
        tone biases, topic preferences and risk profile.
    """

    # Ensure expected structures exist
    emotional = current_state.setdefault("emotional_vector", {})
    tone_bias = current_state.setdefault("tone_bias", {})
    topic_weights = current_state.setdefault("topic_preference_weights", {})

    feedback_memories = [m for m in memory_log if m.get("type") == "feedback"]
    if not feedback_memories:
        return current_state

    tone_scores: Dict[str, List[float]] = {}
    topic_scores: Dict[str, List[float]] = {}

    for mem in feedback_memories:
        resonance = float(mem.get("resonance_score", 0.0))
        tone = mem.get("tone")
        topic = mem.get("topic")
        if tone:
            tone_scores.setdefault(tone, []).append(resonance)
        if topic:
            topic_scores.setdefault(topic, []).append(resonance)

    def _average(scores: List[float]) -> float:
        return sum(scores) / len(scores) if scores else 0.0

    # Tone trend detection
    for tone, scores in tone_scores.items():
        avg = _average(scores)
        if avg > 0.7:
            tone_bias[tone] = tone_bias.get(tone, 0.0) + 0.2
            emotional["valence"] = _clamp(float(emotional.get("valence", 0.0)) + 0.1)
            emotional["arousal"] = _clamp(float(emotional.get("arousal", 0.0)) + 0.2)
        elif avg < 0.3:
            tone_bias[tone] = tone_bias.get(tone, 0.0) - 0.2

    # Topic reinforcement
    for topic, scores in topic_scores.items():
        avg = _average(scores)
        if avg > 0.6:
            topic_weights[topic] = topic_weights.get(topic, 0.0) + 0.3
        elif avg < 0.4:
            topic_weights[topic] = topic_weights.get(topic, 0.0) - 0.2

    # Risk profile adjustment based on recent performance
    recent_scores = [float(m.get("resonance_score", 0.0)) for m in feedback_memories[-3:]]
    underperforming = sum(1 for s in recent_scores if s < 0.5)
    current_state["risk_profile"] = "low" if underperforming >= 2 else "balanced"

    return current_state


__all__ = ["tune_prompt_parameters"]
