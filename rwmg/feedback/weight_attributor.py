"""Utilities for attributing weight to feedback memories.

At the moment only :func:`compute_memory_weight` is implemented.  The rest of the
module intentionally remains minimal as the project skeleton only requires the
core scoring logic.  The function follows the detailed specification provided in
the project brief and converts raw engagement feedback into a normalised
resonance score between ``0.0`` and ``1.0``.
"""

from pathlib import Path
from typing import Dict

import yaml


def _load_event_multiplier(event_type: str) -> float:
    """Fetch the multiplier for ``event_type`` from ``archetype_rules.yaml``.

    If the configuration file or the specific event type cannot be found the
    function gracefully falls back to ``1.0``.
    """

    config_path = Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return 1.0

    return float(rules.get(event_type, {}).get("event_type_multiplier", 1.0))


# feedback/weight_attributor.py
def compute_memory_weight(
    feedback_data: Dict,
    event_type: str,
    agent_state: Dict,
    platform_profile: Dict,
) -> float:
    """Calculate a resonance score for a memory.

    The score is a product of several components:

    * **Raw score** – weighted sum of likes, comments and shares according to
      the platform configuration.
    * **Authenticity multiplier** – proportion of human comments vs. agent
      noise.
    * **Emotional alignment factor** – adjusts the score based on sentiment of
      the received feedback.  Sentiment is expected in the range ``[-1, 1]`` and
      scales the score linearly by ``±0.5``.
    * **Event type multiplier** – amplifies the score based on thematic
      importance defined in ``archetype_rules.yaml``.

    Finally the score is normalised to the ``0.0`` – ``1.0`` range using a
    simple saturation function ``x / (1 + x)``.
    """

    # --- 1. Raw score -----------------------------------------------------
    likes = float(feedback_data.get("total_likes", 0))
    comments = float(feedback_data.get("total_replies", 0))
    shares = float(feedback_data.get("total_shares", 0))

    raw_score = (
        likes * float(platform_profile.get("like_weight", 0.0))
        + comments * float(platform_profile.get("comment_weight", 0.0))
        + shares * float(platform_profile.get("share_weight", 0.0))
    )

    # --- 2. Authenticity multiplier --------------------------------------
    authenticity_multiplier = float(
        max(0.0, min(1.0, feedback_data.get("human_comment_ratio", 0.0)))
    )

    # --- 3. Emotional alignment factor -----------------------------------
    # Feedback sentiment is provided in ``[-1, 1]`` and directly adjusts the
    # score. Positive sentiment boosts the score while negative sentiment
    # dampens it, with a maximum effect of ±50%.
    sentiment = float(feedback_data.get("average_sentiment_score", 0.0))
    sentiment = max(-1.0, min(1.0, sentiment))
    emotional_alignment_factor = 1.0 + (sentiment * 0.5)

    # --- 4. Event type multiplier ----------------------------------------
    event_multiplier = _load_event_multiplier(event_type)

    # --- 5. Final resonance score ----------------------------------------
    final_score = (
        raw_score
        * authenticity_multiplier
        * emotional_alignment_factor
        * event_multiplier
    )

    # --- 6. Normalisation -------------------------------------------------
    if final_score <= 0:
        return 0.0

    normalised_score = final_score / (1.0 + final_score)
    return float(max(0.0, min(1.0, normalised_score)))
