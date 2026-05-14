"""Utilities for weaving a new agent's canonical life events.

The full project envisions a rich narrative generation pipeline.  For the unit
tests we implement a lightweight yet deterministic variant that derives a small
set of formative events from the agent's core archetype.  Each event influences
the starting trait vectors via ``trait_shift`` entries consumed by
``trait_initializer.calculate_initial_traits``.

The event probabilities are primarily sourced from
``config/archetype_rules.yaml``.  When the configuration or the YAML parser is
unavailable a specification conforming fallback mapping is used instead.  To
keep the process reproducible a locally seeded ``random.Random`` instance is
employed, using the agent's UUID (when available) as the seed.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

# ``yaml`` is optional – the module gracefully falls back when it cannot be
# imported or the configuration file is missing.
try:  # pragma: no cover - exercised indirectly
    import yaml  # type: ignore
except Exception:  # ModuleNotFoundError or any other issue
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_event_probabilities() -> Dict[str, Dict[str, float]]:
    """Load archetype specific canonical event probabilities.

    Returns a mapping ``{archetype: {event_type: probability}}`` using the
    project configuration.  A minimal built‑in mapping is provided as a
    fallback to ensure the function remains operational without external
    dependencies.
    """

    config_path = (
        Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"
    )

    fallback = {
        "king": {
            "early_trauma": 0.1,
            "leadership_trial": 0.8,
            "betrayal_of_trust": 0.6,
        },
        "lover": {
            "early_trauma": 0.6,
            "relationship_breakup": 0.9,
            "abandonment": 0.7,
        },
        "warrior": {
            "early_trauma": 0.3,
            "physical_conflict": 0.8,
            "test_of_discipline": 0.9,
        },
        "magician": {
            "early_trauma": 0.4,
            "intellectual_betrayal": 0.7,
            "system_collapse": 0.8,
        },
    }

    if yaml is None:
        return fallback

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return fallback

    probabilities: Dict[str, Dict[str, float]] = {}
    for archetype, info in data.items():
        probs: Dict[str, float] = {}
        for ev, prob in (info.get("event_probabilities") or {}).items():
            try:
                probs[ev] = float(prob)
            except (TypeError, ValueError):
                continue
        probabilities[archetype.lower()] = probs

    return probabilities or fallback


EVENT_PROBABILITIES = _load_event_probabilities()

# Heuristic trait effects associated with canonical event types.  These are
# intentionally small to keep initial traits near the neutral baseline.
EVENT_TRAIT_EFFECTS: Dict[str, List[Dict[str, float]]] = {
    "early_trauma": [
        {"trait": "neuroticism", "value": 0.2},
        {"trait": "valence", "value": -0.3},
        {"trait": "stress", "value": 0.2},
    ],
    "leadership_trial": [
        {"trait": "conscientiousness", "value": 0.3},
        {"trait": "extraversion", "value": 0.2},
    ],
    "betrayal_of_trust": [
        {"trait": "agreeableness", "value": -0.2},
        {"trait": "valence", "value": -0.2},
    ],
    "relationship_breakup": [
        {"trait": "grief", "value": 0.3},
        {"trait": "valence", "value": -0.2},
        {"trait": "extraversion", "value": -0.1},
    ],
    "abandonment": [
        {"trait": "grief", "value": 0.3},
        {"trait": "valence", "value": -0.3},
    ],
    "physical_conflict": [
        {"trait": "anger", "value": 0.2},
        {"trait": "extraversion", "value": 0.2},
    ],
    "test_of_discipline": [
        {"trait": "conscientiousness", "value": 0.3},
    ],
    "intellectual_betrayal": [
        {"trait": "stress", "value": 0.2},
        {"trait": "valence", "value": -0.2},
    ],
    "system_collapse": [
        {"trait": "stress", "value": 0.3},
        {"trait": "neuroticism", "value": 0.2},
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_canonical_events(agent_seed: Dict) -> List[Dict]:
    """Create a deterministic set of canonical events for ``agent_seed``.

    Parameters
    ----------
    agent_seed:
        Mapping produced by ``identity_seed_constructor``.  Only the
        ``"archetype_core"`` and ``"agent_id"`` keys are consulted; missing
        values result in sensible defaults.

    Returns
    -------
    list[dict]
        List of canonical event dictionaries conforming to the project schema.
        At least one event is always produced to ensure subsequent modules have
        data to operate on.
    """

    archetype = (agent_seed.get("archetype_core") or "Lover").lower()
    probs = EVENT_PROBABILITIES.get(archetype, {})

    rng = random.Random(str(agent_seed.get("agent_id", "")))
    events: List[Dict] = []

    for event_type, probability in probs.items():
        try:
            should_create = rng.random() < float(probability)
        except (TypeError, ValueError):
            continue
        if not should_create:
            continue

        age = rng.randint(5, 40)
        timestamp = (
            datetime.now(timezone.utc) - timedelta(days=age * 365)
        ).replace(microsecond=0)

        events.append(
            {
                "event_id": uuid.uuid4().hex,
                "age": age,
                "timestamp": timestamp.isoformat(),
                "type": event_type,
                "description": f"{archetype.capitalize()} experienced {event_type.replace('_', ' ')}",
                "trait_shift": EVENT_TRAIT_EFFECTS.get(event_type, []),
            }
        )

    # Ensure at least one event so downstream processing always has input
    if not events:
        fallback_type = max(probs, key=probs.get) if probs else "origin_story"
        age = rng.randint(5, 40)
        timestamp = (
            datetime.now(timezone.utc) - timedelta(days=age * 365)
        ).replace(microsecond=0)
        events.append(
            {
                "event_id": uuid.uuid4().hex,
                "age": age,
                "timestamp": timestamp.isoformat(),
                "type": fallback_type,
                "description": f"{archetype.capitalize()} experienced {fallback_type.replace('_', ' ')}",
                "trait_shift": EVENT_TRAIT_EFFECTS.get(fallback_type, []),
            }
        )

    return events


