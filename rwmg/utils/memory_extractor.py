"""Utilities for ranking an agent's memories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from .diversity_calculator import compute_diversity_bonus
from .math_functions import exponential_decay
from .timestamp_utils import calculate_age_in_days


# utils/memory_extractor.py
def rank_memories(agent_uuid: str, top_k: int = 5, bottom_k: int = 3) -> Tuple[List[str], List[str]]:
    """Return top and bottom ranked memory texts for an agent.

    Each memory's weight is derived from its stored ``resonance_score`` which is
    reduced over time using an exponential decay and then adjusted by a small
    diversity bonus based on the rarity of its tags.  The resulting weight is
    clamped to the ``[0, 1]`` range and used for ranking.

    Parameters
    ----------
    agent_uuid:
        Identifier for the agent whose memories should be ranked.
    top_k:
        Number of highest weighted memories to return.  Defaults to ``5``.
    bottom_k:
        Number of lowest weighted memories to return.  Defaults to ``3``.

    Returns
    -------
    Tuple[List[str], List[str]]
        Two lists containing the memory ``content`` of the top ``top_k`` and
        bottom ``bottom_k`` memories respectively.  Lists may be shorter if the
        agent has fewer stored memories or if files are missing/invalid.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    if not log_path.exists():
        return [], []

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            log_entries = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return [], []

    tags_path = Path("agents") / agent_uuid / "memory_tags.json"
    try:
        with tags_path.open("r", encoding="utf-8") as fh:
            tag_map = json.load(fh)
    except (json.JSONDecodeError, OSError):
        tag_map = {}

    # store (weight, content, event_id) for downstream caching
    scored: List[Tuple[float, str, str]] = []

    for entry in log_entries:
        event_id = entry.get("event_id")
        content = entry.get("content", "")
        if not content:
            continue

        resonance = float(entry.get("resonance_score", 0.0))
        timestamp = entry.get("timestamp")
        age_days = calculate_age_in_days(timestamp) if timestamp else 0

        decayed = exponential_decay(resonance, age_days, half_life=30)
        tags = tag_map.get(event_id, [])
        diversity_bonus = compute_diversity_bonus(tags, agent_uuid)

        weight = max(0.0, min(1.0, decayed + diversity_bonus))
        scored.append((weight, content, event_id))

    if not scored:
        return [], []

    scored.sort(key=lambda x: x[0])  # ascending by weight

    # Split into top and bottom groups retaining weight and ids
    bottom_entries = scored[:bottom_k]
    top_entries = list(reversed(scored[-top_k:]))

    # Persist the top memories for quick recall
    cache_path = Path("agents") / agent_uuid / "memory_cache_top5.json"
    cache_payload = [
        {"event_id": eid, "content": text, "weight": weight}
        for weight, text, eid in top_entries
    ]
    try:
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(cache_payload, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    # Append/update suppression log with lowest weighted memories
    suppression_path = Path("agents") / agent_uuid / "suppression_log.json"
    try:
        with suppression_path.open("r", encoding="utf-8") as fh:
            suppression_log = json.load(fh)
    except (json.JSONDecodeError, OSError):
        suppression_log = []

    existing = {entry.get("event_id"): entry for entry in suppression_log}
    for weight, text, eid in bottom_entries:
        existing[eid] = {"event_id": eid, "content": text, "weight": weight}

    try:
        with suppression_path.open("w", encoding="utf-8") as fh:
            json.dump(list(existing.values()), fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    bottom = [content for _, content, _ in bottom_entries]
    top = [content for _, content, _ in top_entries]
    return top, bottom


