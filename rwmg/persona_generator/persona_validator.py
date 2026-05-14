"""Persona coherence validation utilities.

This module provides a lightweight validator that inspects a newly generated
persona prior to activation.  The checks focus on structural correctness and
basic logical consistency so that obviously contradictory agents are caught
early in the generation pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

from ..utils.timestamp_utils import calculate_age_in_days


# persona_generator/persona_validator.py
def validate_persona_coherence(persona_data: Dict) -> bool:
    """Checks a newly generated persona for logical contradictions before activation.

    The function performs a series of structural and logical sanity checks on
    ``persona_data``.  The expected structure includes ``profile``,
    ``agent_state`` and ``canonical_events`` sections as described in the
    project schema.  Only inexpensive validations are carried out here; deeper
    semantic checks are deferred to later stages of the lifecycle.

    Parameters
    ----------
    persona_data:
        Aggregated persona information produced during the generation process.

    Returns
    -------
    bool
        ``True`` if the persona passes all checks, otherwise ``False``.
    """

    # --- Required top-level sections ------------------------------------
    for section in ("profile", "agent_state", "canonical_events"):
        if section not in persona_data:
            return False

    profile = persona_data["profile"]
    agent_state = persona_data["agent_state"]
    canonical_events = persona_data["canonical_events"]

    # --- Profile validation ----------------------------------------------
    required_profile_keys = {
        "agent_id",
        "name",
        "birthday",
        "archetype_core",
        "email",
        "secrets_path",
    }
    if not required_profile_keys.issubset(profile):
        return False

    if profile["archetype_core"] not in {"King", "Lover", "Warrior", "Magician"}:
        return False

    try:
        birth_dt = datetime.fromisoformat(profile["birthday"])
    except (TypeError, ValueError):
        return False

    if birth_dt.tzinfo is None:
        birth_dt = birth_dt.replace(tzinfo=timezone.utc)

    if birth_dt > datetime.now(timezone.utc):
        return False

    if profile["agent_id"] != agent_state.get("agent_id"):
        return False

    # Approximate age in years for later checks
    age_years = calculate_age_in_days(profile["birthday"]) // 365

    # --- Agent state validation ------------------------------------------
    for vec_key in ("trait_vector", "emotional_vector", "emotional_state"):
        vec = agent_state.get(vec_key)
        if not isinstance(vec, dict) or not vec:
            return False
        for val in vec.values():
            try:
                num = float(val)
            except (TypeError, ValueError):
                return False
            if not 0.0 <= num <= 1.0:
                return False

    if not isinstance(agent_state.get("current_tone"), str):
        return False

    # Build set of recognised traits for canonical event validation
    known_traits = (
        set(agent_state["trait_vector"])
        | set(agent_state["emotional_vector"])
        | set(agent_state["emotional_state"])
    )

    # --- Canonical event validation --------------------------------------
    if not isinstance(canonical_events, list) or len(canonical_events) == 0:
        return False

    for event in canonical_events:
        if not all(
            k in event for k in ("event_id", "age", "timestamp", "type", "description")
        ):
            return False

        try:
            age = int(event["age"])
            if age < 0 or age > age_years:
                return False
        except (TypeError, ValueError):
            return False

        try:
            datetime.fromisoformat(event["timestamp"])
        except (TypeError, ValueError):
            return False

        for shift in event.get("trait_shift", []) or []:
            trait = shift.get("trait")
            if trait not in known_traits:
                return False
            try:
                delta = float(shift.get("value"))
            except (TypeError, ValueError):
                return False
            if not -1.0 <= delta <= 1.0:
                return False

    return True

