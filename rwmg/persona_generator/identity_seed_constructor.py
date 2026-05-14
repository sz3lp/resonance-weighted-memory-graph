"""Create the initial identity seed for a prospective agent."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Dict


def create_identity_seed(archetype: str, platform_config: Dict) -> Dict:
    """Generate a basic identity profile for a new agent.

    The returned dictionary mirrors the ``profile.json`` schema used by the
    simulation.  Only a small subset of fields are populated, sufficient for
    subsequent modules to flesh out additional details.
    """

    agent_id = uuid.uuid4().hex
    name = f"{archetype}-{agent_id[:8]}"

    # Choose a plausible birthday somewhere between 18 and 40 years ago.
    years = random.randint(18, 40)
    birthday = date.today() - timedelta(days=years * 365)

    email_domain = platform_config.get("email_domain", "example.com")
    profile = {
        "agent_id": agent_id,
        "name": name,
        "birthday": birthday.isoformat(),
        "archetype_core": archetype,
        "email": f"{agent_id}@{email_domain}",
        "secrets_path": str(Path("secrets") / "agent_keys" / f"{agent_id}.json"),
    }

    return profile


__all__ = ["create_identity_seed"]

