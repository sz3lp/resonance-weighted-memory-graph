"""Tools for visualizing an agent's memory weight over time."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List

from ..utils.math_functions import exponential_decay


def _load_json(path: str):
    """Safely load JSON returning ``None`` when the file does not exist."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def visualize_memory_history(agent_uuid: str) -> None:
    """Generate a timeline plot for the highest weighted memory.

    The function finds the memory with the greatest current weight from
    ``memory_cache_top5.json`` and visualises how its influence decays over
    time.  If ``matplotlib`` is available a PNG is written beside the agent's
    files; otherwise a JSON representation of the timeline is printed to
    stdout.
    """

    base_dir = os.path.join("rwmg", "agents", agent_uuid)
    cache_path = os.path.join(base_dir, "memory_cache_top5.json")
    log_path = os.path.join(base_dir, "memory_log.json")

    cache = _load_json(cache_path) or []
    if not cache:
        return

    top_memory = cache[0]
    event_id = top_memory.get("event_id")
    initial_weight = float(top_memory.get("weight", 0.0))

    log = _load_json(log_path) or []
    event = next((e for e in log if e.get("event_id") == event_id), None)
    if not event:
        return

    start_time = datetime.fromisoformat(event.get("timestamp"))
    now = datetime.utcnow()
    days = max((now - start_time).days, 1)

    timeline_dates: List[datetime] = []
    timeline_weights: List[float] = []

    for day in range(days + 1):
        timeline_dates.append(start_time + timedelta(days=day))
        timeline_weights.append(
            exponential_decay(initial_weight, day, half_life=30)
        )

    try:
        import matplotlib.pyplot as plt  # type: ignore

        plt.figure(figsize=(6, 3))
        plt.plot(timeline_dates, timeline_weights, marker="o")
        plt.title(f"Memory weight history: {event_id}")
        plt.xlabel("Date")
        plt.ylabel("Weight")
        plt.tight_layout()
        out_path = os.path.join(base_dir, f"{event_id}_history.png")
        plt.savefig(out_path)
        plt.close()
    except Exception:
        # Fallback textual representation
        printable = {
            d.isoformat(): w for d, w in zip(timeline_dates, timeline_weights)
        }
        import logging

        logging.getLogger("rwmg.explainability").info(
            "memory timeline fallback: %s",
            json.dumps({"event_id": event_id, "timeline": printable}, indent=2),
        )

