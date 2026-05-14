"""Derive an agent's initial psychological profile from canonical events."""

from __future__ import annotations

from typing import Dict, List

from ..utils.math_functions import nonlinear_trait_shift


# persona_generator/trait_initializer.py
def calculate_initial_traits(canonical_events: List[Dict]) -> Dict:
    """Calculate starting traits and emotions for a new agent.

    The function iterates over ``canonical_events`` applying the provided
    ``trait_shift`` deltas to three internal vectors:

    ``trait_vector``
        Big Five personality traits in the ``[0, 1]`` range.
    ``emotional_vector``
        High level Valence–Arousal–Dominance representation.
    ``emotional_state``
        Discrete emotions such as joy and anger.

    Shifts are combined using :func:`nonlinear_trait_shift` to keep values within
    bounds.  A simple heuristic derives an initial ``current_tone`` from the
    resulting valence/arousal pair.

    Parameters
    ----------
    canonical_events:
        Sequence of canonical events, each possibly containing a
        ``trait_shift`` list with ``{"trait": str, "value": float}`` entries.

    Returns
    -------
    Dict
        Mapping with ``trait_vector``, ``emotional_vector``, ``emotional_state``
        and ``current_tone`` keys describing the starting agent state.
    """

    # --- Baseline vectors -------------------------------------------------
    trait_vector: Dict[str, float] = {
        "openness": 0.5,
        "conscientiousness": 0.5,
        "extraversion": 0.5,
        "agreeableness": 0.5,
        "neuroticism": 0.5,
    }

    emotional_vector: Dict[str, float] = {
        "valence": 0.5,
        "arousal": 0.5,
        "dominance": 0.5,
    }

    emotional_state: Dict[str, float] = {
        "joy": 0.5,
        "anger": 0.5,
        "grief": 0.5,
        "contempt": 0.5,
        "affinity": 0.5,
        "stress": 0.5,
    }

    # --- Apply trait shifts from canonical events ------------------------
    for event in canonical_events or []:
        for shift in event.get("trait_shift", []) or []:
            trait = shift.get("trait")
            try:
                delta = float(shift.get("value", 0.0))
            except (TypeError, ValueError):
                continue

            if trait in trait_vector:
                trait_vector[trait] = nonlinear_trait_shift(
                    trait_vector[trait], delta
                )
            elif trait in emotional_vector:
                emotional_vector[trait] = nonlinear_trait_shift(
                    emotional_vector[trait], delta
                )
            elif trait in emotional_state:
                emotional_state[trait] = nonlinear_trait_shift(
                    emotional_state[trait], delta
                )

    # --- Deduce initial tone --------------------------------------------
    valence = emotional_vector["valence"]
    arousal = emotional_vector["arousal"]

    if valence > 0.6 and arousal > 0.6:
        tone = "excited"
    elif valence > 0.6:
        tone = "warm"
    elif valence < 0.4 and arousal > 0.6:
        tone = "agitated"
    elif valence < 0.4:
        tone = "melancholic"
    else:
        tone = "neutral"

    return {
        "trait_vector": trait_vector,
        "emotional_vector": emotional_vector,
        "emotional_state": emotional_state,
        "current_tone": tone,
    }

