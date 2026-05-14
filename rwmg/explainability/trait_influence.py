"""Analyse how feedback altered an agent's internal state.

The project blueprint describes a feedback loop in which every post can
influence an agent's emotions and long‑term personality traits.  This module
provides a light‑weight, file based implementation of that analysis.  Given a
``event_id`` corresponding to a post, :func:`analyze_trait_shift` locates the
originating agent, loads the associated feedback data and simulates how the
post would have nudged the agent's emotional and trait vectors.  The function
is intentionally conservative – it never mutates files on disk – and simply
returns a report of the calculated deltas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Return ``value`` constrained to the inclusive ``[low, high]`` range."""

    return max(low, min(high, value))


# explainability/trait_influence.py
def analyze_trait_shift(event_id: str) -> Dict:
    """Analyse the impact of a post on an agent's emotions and traits.

    Parameters
    ----------
    event_id:
        Identifier of the post to analyse.

    Returns
    -------
    dict
        Contains the ``agent_id`` and dictionaries describing the shift in the
        agent's ``emotional_state`` and ``trait_vector``.  Empty if the event
        cannot be located.
    """

    agents_root = Path(__file__).resolve().parents[1] / "agents"

    for agent_dir in agents_root.iterdir():
        if not agent_dir.is_dir():
            continue

        log_path = agent_dir / "memory_log.json"
        state_path = agent_dir / "agent_state.json"

        if not log_path.exists() or not state_path.exists():
            continue

        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh)
            with state_path.open("r", encoding="utf-8") as fh:
                agent_state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for entry in log_entries:
            if entry.get("event_id") != event_id:
                continue

            feedback_data: Dict = {}
            feedback_ref = entry.get("feedback_data_ref")
            if isinstance(feedback_ref, str) and feedback_ref:
                candidate_paths = [
                    agent_dir / feedback_ref,
                    Path(feedback_ref),
                    agents_root.parent / feedback_ref,
                ]
                for path in candidate_paths:
                    if path.exists():
                        try:
                            with path.open("r", encoding="utf-8") as fh:
                                feedback_data = json.load(fh)
                        except (json.JSONDecodeError, OSError):
                            pass
                        break

            resonance = float(entry.get("resonance_score", 0.0))
            sentiment = float(feedback_data.get("average_sentiment_score", 0.0))
            human_ratio = float(feedback_data.get("human_comment_ratio", 0.0))

            # --- Emotional shift -------------------------------------------
            emo_before = agent_state.get("emotional_state", {})
            emo_after = {}
            emo_delta = {}
            for key, value in emo_before.items():
                val = float(value)
                new_val = _clamp(val + sentiment * resonance * 0.1)
                emo_after[key] = new_val
                emo_delta[key] = new_val - val

            # --- Trait shift -----------------------------------------------
            trait_before = agent_state.get("trait_vector", {})
            trait_after = {}
            trait_delta = {}
            for key, value in trait_before.items():
                val = float(value)
                new_val = _clamp(val + sentiment * human_ratio * resonance * 0.05)
                trait_after[key] = new_val
                trait_delta[key] = new_val - val

            return {
                "agent_id": agent_dir.name,
                "event": entry,
                "emotional_shift": emo_delta,
                "updated_emotional_state": emo_after,
                "trait_shift": trait_delta,
                "updated_trait_vector": trait_after,
            }

    return {}


