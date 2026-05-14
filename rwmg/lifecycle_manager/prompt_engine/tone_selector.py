"""Tone selection based on the agent's emotional state."""

from __future__ import annotations

from typing import Dict, Optional


def select_tone(emotional_vector: Dict, community_tone: Optional[str] = None) -> str:
    """Choose a writing tone derived from emotional state and community cues.

    The heuristic is intentionally simple: ``valence`` influences whether the
    tone is positive or negative, while ``arousal`` modulates the energy level.
    If values are missing the function falls back to a neutral tone.  When
    ``community_tone`` is provided it is prefixed to the computed tone to blend
    the agent's state with community expectations.
    """

    valence = float(emotional_vector.get("valence", 0.5))
    arousal = float(emotional_vector.get("arousal", 0.5))

    if valence >= 0.6:
        base = "warm"
    elif valence <= 0.4:
        base = "somber"
    else:
        base = "neutral"

    if arousal > 0.6:
        modifier = "energetic"
    elif arousal < 0.4:
        modifier = "calm"
    else:
        modifier = "steady"

    if base == "neutral" and modifier in {"steady", "calm"}:
        tone = "conversational"
    else:
        tone = f"{modifier} {base}".strip()
    if community_tone:
        return f"{community_tone} {tone}".strip()
    return tone


__all__ = ["select_tone"]

