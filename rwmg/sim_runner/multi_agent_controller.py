
"""Coordinate tasks for multiple agents including social interactions.

In the broader simulation the controller is responsible for deciding what each
agent does during a simulation tick.  Posting and commenting are handled by
other modules; this controller adds support for **upvote** and **downvote**
interactions so that agents can react to existing content.

The implementation here is deliberately small and deterministic so that unit
tests can exercise the behaviour without relying on network calls or external
services.


"""Asynchronous multi-agent controller for RWMG simulations.

This module provides a light-weight orchestrator capable of running
hundreds of agents concurrently.  It is intentionally conservative and
uses best-effort operations so that missing configuration or unexpected
runtime errors do not halt the swarm.  The controller replaces the
``run_epoch`` approach with an event driven system.
"""Asynchronous multi-agent controller for the RWMG simulation.

This module replaces the sequential ``run_epoch`` loop with a stochastic
scheduler that dispatches agents over the course of a simulated day.  It is
purposefully lightweight yet provides hooks for behaviour profiling,
proxy isolation and basic cooldown management.


"""

from __future__ import annotations


from typing import Dict, Iterable, List

from rwmg.sim_runner.social_activity_simulator import evaluate_vote


def assign_tasks(agent_uuid: str, content_feed: Iterable[str]) -> Dict[str, List[Dict[str, str]]]:
    """Return a task bundle for ``agent_uuid`` based on ``content_feed``.

    The ``content_feed`` represents posts visible to the agent.  For each post
    the agent may choose to upvote or downvote depending on how the content
    aligns with its memories.  Neutral posts are ignored.  The returned
    dictionary currently only contains a ``"votes"`` entry but the structure
    mirrors what a fuller controller would provide when also scheduling posting
    or commenting tasks.
    """

    votes: List[Dict[str, str]] = []
    for post in content_feed:
        decision = evaluate_vote(agent_uuid, post)
        if decision != "neutral":
            votes.append({"action": decision, "post": post})

    return {"votes": votes}


__all__ = ["assign_tasks"]

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

from rwmg.sim_runner.epoch_runner import execute_daily_ritual

try:  # ``yaml`` is an optional dependency in the test environment
    import yaml
except Exception:  # pragma: no cover - fallback when PyYAML is missing
    yaml = None  # type: ignore


# Controller state is stored at module level so that helper functions can
# operate without threading through a complex context object.
CONTROLLER_STATE: Dict[str, Dict[str, Any]] = {}


def _load_json(path: Path) -> Dict[str, Any]:
    """Best effort JSON loader returning an empty dict on failure."""

    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Return YAML content or ``{}`` when unavailable."""

    if yaml is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from rwmg.lifecycle_manager.evolution_protocol import check_for_evolution
from rwmg.lifecycle_manager.main_loop import run_agent_day
from rwmg.lifecycle_manager.ritual_tracker import complete_ritual, start_ritual
from rwmg.sim_runner.agent_watcher import log_global_metrics
from rwmg.utils.api_wrappers import _load_proxy_for_agent

# ---------------------------------------------------------------------------
# Helper loaders
# ---------------------------------------------------------------------------

def _load_posting_weight(agent_id: str) -> float:
    """Return the posting likelihood for ``agent_id``.

    The value is stored in ``agents/<id>/agent_state.json`` under the key
    ``posting_likelihood``.  Missing files or malformed JSON default to ``1``.
    """

    state_path = Path("agents") / agent_id / "agent_state.json"
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            return float(data.get("posting_likelihood", 1))
    except Exception:
        return 1.0

def _load_behavior_profiles() -> Dict[str, Dict]:
    """Load optional behaviour profiles describing wake/sleep cycles."""

    config_path = Path("config") / "agent_behavior_profiles.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as fh:

            return yaml.safe_load(fh) or {}
    except Exception:
        return {}



def initialize_controller(
    agent_manifest: Dict[str, Any] | None = None,
    behavior_profiles: Dict[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Load agents, attach behaviour profiles and prepare runtime state.

    Parameters
    ----------
    agent_manifest:
        Optional manifest data.  When ``None`` the content of
        ``agents/persona_manifest.json`` is used.
    behavior_profiles:
        Optional behaviour profile definitions.  When ``None`` the content
        of ``config/agent_behavior_profiles.yaml`` is loaded.
    """

    global CONTROLLER_STATE

    if agent_manifest is None:
        agent_manifest = _load_json(Path("agents") / "persona_manifest.json")

    if behavior_profiles is None:
        behavior_profiles = _load_yaml(Path("config") / "agent_behavior_profiles.yaml")

    proxies = _load_json(Path("secrets") / "proxies_map.json")

    state: Dict[str, Dict[str, Any]] = {}
    for agent_id, meta in (agent_manifest or {}).items():
        if not isinstance(meta, dict) or meta.get("status", "active") != "active":
            continue

        behaviour_name = meta.get("behavior_profile")
        behaviour = behavior_profiles.get(behaviour_name, {}) if behavior_profiles else {}

        state_path = Path("agents") / agent_id / "agent_state.json"
        agent_state = _load_json(state_path)
        agent_state.setdefault("behavior_profile", behaviour_name)
        agent_state.setdefault("behavior", behaviour)

        state[agent_id] = {
            "id": agent_id,
            "meta": meta,
            "agent_state": agent_state,
            "behavior": behaviour,
            "task_queue": [],
            "daily_log": [],
            "next_available_time": datetime.min,
            "proxy": proxies.get(agent_id),
            "state_path": state_path,
        }

    CONTROLLER_STATE = state
    return CONTROLLER_STATE


def _within_windows(current_time: datetime, windows: List[str]) -> bool:
    """Return ``True`` if ``current_time`` is inside any of ``windows``.

    Each window is expected in ``HH:MM-HH:MM`` or ``HH:MM–HH:MM`` format.  The
    function is resilient to malformed entries and simply ignores them.
    """

    if not windows:
        return True

    time_only = current_time.time()
    for window in windows:
        try:
            start_s, end_s = window.replace("–", "-").split("-")
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
        except Exception:
            continue
        if start_t <= time_only <= end_t:
            return True
    return False


def assign_tasks(agent: Dict[str, Any], current_time: datetime) -> None:
    """Enqueue a randomized task for ``agent`` when eligible."""

    state = agent.get("agent_state", {})
    next_avail = state.get("next_available_time")
    if isinstance(next_avail, str):
        try:
            next_avail_dt = datetime.fromisoformat(next_avail)
        except ValueError:
            next_avail_dt = datetime.min
    else:
        next_avail_dt = next_avail or datetime.min
    if current_time < next_avail_dt:
        return

    behaviour = agent.get("behavior", {})
    windows = behaviour.get("activity_windows", [])
    if windows and not _within_windows(current_time, windows):
        return

    posting_likelihood = state.get("posting_likelihood", 1.0)
    if random.random() > float(posting_likelihood or 0):
        return

    task = random.choice(["post", "comment", "engage", "observe", "check_feedback"])
    agent.setdefault("task_queue", []).append(task)


async def execute_task(agent_id: str, task_type: str) -> None:
    """Execute ``task_type`` for ``agent_id`` and update state."""

    agent = CONTROLLER_STATE.get(agent_id)
    if not agent:
        return

    state_path: Path = agent.get("state_path")
    state = agent.get("agent_state", {})
    behaviour = agent.get("behavior", {})

    # Perform the actual task.  Only ``post`` maps to a real routine; other
    # tasks are placeholders which simply yield to the event loop.
    try:
        if task_type == "post":
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, execute_daily_ritual, agent_id)
        else:  # simulate non-post tasks
            await asyncio.sleep(0)
    except Exception as exc:  # pragma: no cover - best effort logging
        logging.exception("task %s for %s failed: %s", task_type, agent_id, exc)

    now = datetime.utcnow()
    cooldown = behaviour.get("cooldown_period_hours", 0)
    if isinstance(cooldown, list) and cooldown:
        cooldown_hours = random.uniform(cooldown[0], cooldown[-1])
    else:
        try:
            cooldown_hours = float(cooldown)
        except Exception:
            cooldown_hours = 0.0

    next_available = now + timedelta(hours=cooldown_hours)

    state.update(
        {
            "last_task": task_type,
            "last_task_time": now.isoformat(),
            "cooldown_timer": cooldown_hours,
            "next_available_time": next_available.isoformat(),
        }
    )
    agent["agent_state"] = state
    agent.setdefault("daily_log", []).append({"task": task_type, "time": now.isoformat()})

    try:  # persist updated agent state
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:  # pragma: no cover - ignore FS errors
        pass


async def run_swarm(
    epoch_duration_hours: int = 24,
    tick_seconds: int = 10,
    time_scale: float = 1.0,
) -> None:
    """Run the swarm controller for ``epoch_duration_hours`` simulated hours.

    ``time_scale`` determines how many simulated seconds elapse per real
    second.  A value of ``12`` would make one simulated hour pass in five real
    minutes.  The function returns when the simulated time exceeds the desired
    duration.
    """

    start_real = datetime.utcnow()
    start_sim = start_real
    end_sim = start_sim + timedelta(hours=epoch_duration_hours)

    metrics = {agent_id: {"tasks": 0} for agent_id in CONTROLLER_STATE}

    while True:
        now_real = datetime.utcnow()
        sim_now = start_sim + (now_real - start_real) * time_scale
        if sim_now >= end_sim:
            break

        # Determine which agents should act this tick
        for agent in CONTROLLER_STATE.values():
            assign_tasks(agent, sim_now)

        jobs = []
        for agent_id, agent in CONTROLLER_STATE.items():
            queue = agent.get("task_queue", [])
            if queue:
                task_type = queue.pop(0)
                jobs.append(asyncio.create_task(execute_task(agent_id, task_type)))
                metrics[agent_id]["tasks"] += 1

        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

        await asyncio.sleep(tick_seconds / time_scale)

    # Persist metrics for external analysis
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"swarm_metrics_{start_sim.date().isoformat()}.json"
    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2, default=str)
    except OSError:  # pragma: no cover - best effort
        pass


__all__ = [
    "initialize_controller",
    "run_swarm",
    "assign_tasks",
    "execute_task",
]

def _is_awake(agent_id: str, minute_of_day: int, profiles: Dict[str, Dict]) -> bool:
    """Determine whether ``agent_id`` is awake at ``minute_of_day``."""

    profile = profiles.get(agent_id) or {}
    wake = int(profile.get("wake_hour", 0)) * 60
    sleep = int(profile.get("sleep_hour", 24)) * 60
    if wake <= sleep:
        return wake <= minute_of_day < sleep
    # handle cycles that wrap past midnight
    return minute_of_day >= wake or minute_of_day < sleep

# ---------------------------------------------------------------------------
# Core async execution
# ---------------------------------------------------------------------------

async def _execute_agent(agent_id: str) -> None:
    """Run the daily ritual for ``agent_id`` with retry protection."""

    proxy = _load_proxy_for_agent(agent_id)
    if proxy is None:
        # Without a proxy we skip to maintain network isolation expectations
        return

    start_ritual(agent_id, "posting")
    try:
        for attempt in range(3):
            try:
                run_agent_day(agent_id, 0, proxies=proxy)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
        try:
            check_for_evolution(agent_id)
        except Exception:
            pass
    finally:
        complete_ritual(agent_id)


async def run_simulated_day(
    agent_manifest: Dict[str, Dict],
    timestep_minutes: int = 10,
    real_time: bool = False,
) -> None:
    """Run a 24 hour simulation period for ``agent_manifest`` asynchronously."""

    behaviour_profiles = _load_behavior_profiles()
    cooldown: Dict[str, datetime] = {}
    start_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    current_time = start_time
    end_time = start_time + timedelta(days=1)

    while current_time < end_time:
        minute_of_day = int((current_time - start_time).total_seconds() // 60)

        # Determine eligible agents and their weights
        candidates: List[str] = []
        weights: List[float] = []
        for agent_id in agent_manifest:
            if not _is_awake(agent_id, minute_of_day, behaviour_profiles):
                continue
            if cooldown.get(agent_id, start_time) > current_time:
                continue
            candidates.append(agent_id)
            weights.append(_load_posting_weight(agent_id))

        if candidates:
            sample_size = max(1, int(len(candidates) * 0.1))
            selected = set(random.choices(candidates, weights=weights, k=sample_size))
            tasks = [asyncio.create_task(_execute_agent(a)) for a in selected]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            cooldown_update = current_time + timedelta(minutes=30)
            for a in selected:
                cooldown[a] = cooldown_update

        log_global_metrics(agent_manifest, minute_of_day // 60)

        # Advan lo ce simulation clock
        current_time += timedelta(minutes=timestep_minutes)
        await asyncio.sleep(timestep_minutes * 60 if real_time else 0)


__all__ = ["run_simulated_day"]

