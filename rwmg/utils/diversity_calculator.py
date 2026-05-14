"""Utilities for computing memory tag diversity bonuses."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import List


RECENT_WINDOW = 50  # Number of most recent memories to consider when scoring


def compute_diversity_bonus(memory_tags: List[str], agent_uuid: str) -> float:
    """Return a small bonus encouraging thematic diversity.

    The bonus is derived from the rarity of the provided ``memory_tags`` when
    compared against the agent's existing tag usage. Only the tags from the
    agent's most recent memories are considered (up to ``RECENT_WINDOW``
    entries) so that long forgotten themes do not permanently penalise new
    experiences. Tags that rarely appear in this recent set receive a higher
    bonus, while frequently used tags yield little to no bonus. The result is a
    value between ``0`` and ``0.3`` which can be added to a memory's weight
    prior to ranking.

    Parameters
    ----------
    memory_tags:
        Tags generated for the candidate memory.
    agent_uuid:
        Identifier used to locate the agent's stored tag history.

    Returns
    -------
    float
        A diversity bonus in the range ``0``–``0.3``. ``0`` indicates that all
        tags are already common in the agent's recent memory set, while ``0.3``
        denotes entirely novel tags.
    """

    if not memory_tags:
        return 0.0

    tags_path = Path("agents") / agent_uuid / "memory_tags.json"
    if not tags_path.exists():
        # No historical tag data – treat all tags as novel.
        return 0.3

    try:
        with tags_path.open("r", encoding="utf-8") as f:
            tag_map = json.load(f)
    except Exception:
        # If the file can't be read or is invalid, fall back to no bonus to
        # avoid unpredictable behaviour.
        return 0.0

    # Determine which event IDs are considered "recent".  If ``memory_log`` is
    # available we use its chronological ordering, otherwise we fall back to all
    # known tags.
    log_path = Path("agents") / agent_uuid / "memory_log.json"
    recent_event_ids: List[str] = []
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as f:
                log_entries = json.load(f)
            # Sort by timestamp to guard against out-of-order logs and then take
            # the most recent ``RECENT_WINDOW`` entries.
            log_entries.sort(key=lambda e: e.get("timestamp", ""))
            recent_event_ids = [e.get("event_id") for e in log_entries[-RECENT_WINDOW:]]
        except Exception:
            recent_event_ids = []

    if recent_event_ids:
        recent_tags = [tag for eid in recent_event_ids for tag in tag_map.get(eid, [])]
    else:
        # Fall back to all stored tags if we cannot determine recency.
        recent_tags = [tag for tags in tag_map.values() for tag in tags]

    if not recent_tags:
        return 0.3

    tag_counts = Counter(recent_tags)
    total = sum(tag_counts.values())
    if not total:
        return 0.3

    # Compute rarity for each candidate tag: 1 minus its relative frequency.
    rarity_scores = []
    for tag in set(memory_tags):  # Deduplicate incoming tags for fairness
        frequency = tag_counts.get(tag, 0) / total
        rarity_scores.append(1 - frequency)

    avg_rarity = sum(rarity_scores) / len(rarity_scores)

    # Scale to a maximum bonus of 0.3 and clamp to [0, 0.3].
    bonus = max(0.0, min(0.3, avg_rarity * 0.3))
    return bonus

