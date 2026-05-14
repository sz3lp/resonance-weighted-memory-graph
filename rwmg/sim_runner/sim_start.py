"""Utilities for bootstrapping the simulation by creating agents.

The real project would involve a fairly involved pipeline for birthing an
agent – generating a persona, seeding memories and provisioning credentials.
For the purposes of the unit tests we implement a much lighter version that is
still file‑system driven and therefore compatible with the rest of the
modules.  Each created agent receives a directory under ``agents/`` containing
basic placeholder files so downstream functions can operate without failing.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Dict, List

import yaml

from rwmg.utils.timestamp_utils import get_current_iso_time


def _load_behavior_profiles() -> Dict[str, Dict]:
    """Read behaviour templates from the config file."""

    config_path = (
        Path(__file__).resolve().parent.parent / "config" / "agent_behavior_profiles.yaml"
    )
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


BEHAVIOR_PROFILES = _load_behavior_profiles()


def assign_behavior_profile(agent_id: str) -> str:
    """Randomly choose a behaviour profile for ``agent_id``."""

    if not BEHAVIOR_PROFILES:
        return ""
    return random.choice(list(BEHAVIOR_PROFILES.keys()))


# sim_runner/sim_start.py
def create_agents(count: int, archetype_config: Dict, platform_config: Dict) -> Dict[str, str]:
    """Initialise a number of simple agents on disk.

    Parameters
    ----------
    count:
        Number of agents to create.  Values below zero result in an empty
        mapping being returned.
    archetype_config:
        Configuration describing available archetypes.  The function expects a
        list under ``"archetypes"`` or, alternatively, uses the dictionary keys
        as the available archetypes.  If no archetype information is supplied a
        default of ``"Lover"`` is used.
    platform_config:
        Used to derive basic communication details for the agent.  Currently
        only an ``"email_domain"`` key is observed.

    Returns
    -------
    Dict[str, str]
        Mapping of generated agent UUIDs to their display names.
    """

    agents: Dict[str, str] = {}
    if count <= 0:
        return agents

    # Determine available archetypes; tolerate a variety of config shapes.
    if isinstance(archetype_config, dict):
        if isinstance(archetype_config.get("archetypes"), list):
            archetypes = list(archetype_config["archetypes"])
        else:
            archetypes = list(archetype_config.keys())
    elif isinstance(archetype_config, list):
        archetypes = list(archetype_config)
    else:
        archetypes = []
    if not archetypes:
        archetypes = ["Lover"]

    email_domain = str(platform_config.get("email_domain", "example.com"))

    root_dir = Path(__file__).resolve().parent.parent
    base_agents = root_dir / "agents"
    secrets_dir = root_dir / "secrets" / "agent_keys"
    secrets_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(count):
        agent_id = uuid.uuid4().hex
        archetype = random.choice(archetypes)
        name = f"Agent-{agent_id[:8]}"

        agent_dir = base_agents / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        profile = {
            "agent_id": agent_id,
            "name": name,
            "birthday": get_current_iso_time().split("T")[0],
            "archetype_core": archetype,
            "email": f"{agent_id}@{email_domain}",
            "secrets_path": str(secrets_dir / f"{agent_id}.json"),
        }

        # Persist the profile and a handful of stub files used by other
        # modules.  Failures in writing non‑critical files are ignored so that
        # agent creation remains best‑effort.
        try:
            with (agent_dir / "profile.json").open("w", encoding="utf-8") as fh:
                json.dump(profile, fh, ensure_ascii=False, indent=2)

            behavior = assign_behavior_profile(agent_id)
            placeholders = {
                "agent_state.json": {"behavior_profile": behavior},
                "canonical_events.json": [],
                "memory_log.json": [],
                "memory_cache_top5.json": [],
                "suppression_log.json": [],
                "memory_tags.json": {},
                "connections.json": {"friends": [], "mentors": []},
            }
            for filename, default in placeholders.items():
                with (agent_dir / filename).open("w", encoding="utf-8") as fh:
                    json.dump(default, fh, ensure_ascii=False, indent=2)

            (agent_dir / "memory_index.csv").touch()
            (agent_dir / "temp").mkdir(exist_ok=True)

            secret_path = secrets_dir / f"{agent_id}.json"
            if not secret_path.exists():
                with secret_path.open("w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "email_provider": "",
                            "smtp_user": "",
                            "smtp_pass": "",
                        },
                        fh,
                        ensure_ascii=False,
                        indent=2,
                    )
        except OSError:
            # Ignore file system errors to keep agent creation resilient.
            pass

        agents[agent_id] = name

    return agents


def populate_manifest(agent_uuids: List[str]) -> None:
    """Create or update the global agent manifest.

    The manifest lives at ``agents/persona_manifest.json`` and stores a small
    amount of metadata for each active agent.  Existing entries are preserved
    and updated when a UUID reappears in ``agent_uuids``.
    """

    root_dir = Path(__file__).resolve().parent.parent
    manifest_path = root_dir / "agents" / "persona_manifest.json"

    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, json.JSONDecodeError):
        manifest = {}

    for agent_id in agent_uuids:
        profile_path = root_dir / "agents" / agent_id / "profile.json"
        try:
            with profile_path.open("r", encoding="utf-8") as fh:
                profile = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue

        manifest[agent_id] = {
            "name": profile.get("name", ""),
            "archetype_core": profile.get("archetype_core", ""),
            "email": profile.get("email", ""),
            "status": "active",
            "created_at": get_current_iso_time(),
        }

    try:
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Failure to persist the manifest is non-fatal for the simulation.
        pass

