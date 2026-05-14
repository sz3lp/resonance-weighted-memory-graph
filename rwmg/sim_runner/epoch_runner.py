"""Orchestrate the simulation epochs."""

from __future__ import annotations
from pathlib import Path
from typing import Dict

from rwmg.lifecycle_manager.evolution_protocol import check_for_evolution
from rwmg.lifecycle_manager.main_loop import run_agent_day
from rwmg.lifecycle_manager.ritual_tracker import complete_ritual, start_ritual
from rwmg.sim_runner.agent_watcher import log_global_metrics


def run_epoch(agent_manifest: Dict, epoch_length: int) -> None:
    """Run ``epoch_length`` days of simulation for each agent.

    The function is intentionally defensive: misconfigured manifests or
    transient failures in a single agent must not halt the simulation.  Only
    agents marked as ``active`` and whose directories exist are processed.
    """

    if not isinstance(agent_manifest, dict) or epoch_length <= 0:
        return

    for day in range(epoch_length):
        for agent_uuid, meta in list(agent_manifest.items()):
            status = meta.get("status", "active") if isinstance(meta, dict) else "active"
            if status != "active":
                continue

            if not (Path("agents") / agent_uuid).exists():
                continue

            try:
                execute_daily_ritual(agent_uuid)
            except Exception:
                # Keep the epoch running even if an individual agent fails.
                continue

        log_global_metrics(agent_manifest, day)


def execute_daily_ritual(agent_uuid: str) -> None:

    """Perform the posting cycle and evolution check for ``agent_uuid``."""

    start_ritual(agent_uuid, "posting")
    try:
        run_agent_day(agent_uuid, 0)
    finally:
        complete_ritual(agent_uuid)

    try:
        check_for_evolution(agent_uuid)
    except Exception:
        # Evolution checks are best-effort; failures are ignored.
        pass


__all__ = ["run_epoch", "execute_daily_ritual"]

