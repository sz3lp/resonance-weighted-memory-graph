"""Utility for creating email credentials for new agents."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Dict


def build_email_identity(agent_uuid: str, email_provider_config: Dict) -> Dict:
    """Generate and persist email credentials for an agent.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent.
    email_provider_config:
        Configuration dictionary describing the email provider.  The keys
        ``"domain"`` and ``"provider"`` are recognised but both are optional.

    Returns
    -------
    Dict
        Mapping containing the email address and path to the stored secrets
        file.  The function is forgiving: I/O failures simply result in the
        credentials not being written.
    """

    domain = email_provider_config.get("domain", "example.com")
    provider = email_provider_config.get("provider", "mockmail")

    address = f"{agent_uuid}@{domain}"
    password = secrets.token_hex(16)

    secrets_dir = Path("secrets") / "agent_keys"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secrets_dir / f"{agent_uuid}.json"

    payload = {
        "email_provider": provider,
        "smtp_user": address,
        "smtp_pass": password,
    }

    try:
        with secret_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Best-effort; failure to persist credentials is non-fatal.
        pass

    return {"email": address, "secrets_path": str(secret_path)}


__all__ = ["build_email_identity"]

