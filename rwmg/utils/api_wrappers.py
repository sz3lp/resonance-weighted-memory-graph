"""Wrappers around external APIs used by the simulation.

The functions in this module provide thin abstractions over HTTP endpoints for the
Gemini LLM API and various social platforms.  They are intentionally lightweight
so that higher level modules can mock or swap them easily during testing.

Both helpers read API keys from ``secrets/platform_keys.json`` which lives at the
root of the repository.  The file is expected to contain keys such as
``gemini_api_key`` and platform tokens.  A small amount of defensive programming
is included so that missing keys or network failures raise informative errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

try:  # pragma: no cover - import is trivial but may fail in minimal envs
    import requests
except Exception:  # pragma: no cover - handled at call time
    requests = None


_ROOT = Path(__file__).resolve().parents[1]


def _load_platform_keys() -> Dict[str, str]:
    """Return the dictionary of platform API keys.

    Missing or malformed files yield an empty dictionary which callers can handle
    gracefully.  This function is separated for ease of mocking during tests.
    """

    key_path = _ROOT / "secrets" / "platform_keys.json"
    try:
        with key_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_proxy_for_agent(agent_id: str) -> Optional[Dict[str, str]]:
    """Return the proxy configuration for ``agent_id``.

    The proxies are stored in ``secrets/proxies_map.json`` using the format

    ``{"agent_id": "http://user:pass@proxy:port"}``.

    Parameters
    ----------
    agent_id:
        Identifier for the agent whose proxy should be loaded.

    Returns
    -------
    Optional[Dict[str, str]]
        A ``requests`` compatible proxies dictionary or ``None`` if no mapping
        exists.
    """

    proxy_path = _ROOT / "secrets" / "proxies_map.json"
    try:
        with proxy_path.open("r", encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    proxy_url = mapping.get(agent_id)
    if not proxy_url:
        return None

    return {"http": proxy_url, "https": proxy_url}


def call_gemini_api(prompt: str, proxies: Optional[Dict[str, str]] = None) -> str:
    """Send ``prompt`` to the Gemini API and return the model's text output.

    Parameters
    ----------
    prompt:
        The textual prompt to submit to Gemini.

    Returns
    -------
    str
        The model generated text.

    Raises
    ------
    RuntimeError
        If the request fails or the API key is missing.
    """

    if requests is None:  # pragma: no cover - trivial in tests
        raise RuntimeError("The 'requests' library is required to call the Gemini API")

    api_key = _load_platform_keys().get("gemini_api_key")
    if not api_key:
        raise RuntimeError("Gemini API key not found in secrets/platform_keys.json")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-pro:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, json=payload, timeout=10, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:  # pragma: no cover - network may be unavailable
        raise RuntimeError(f"Gemini API request failed: {exc}") from exc


def post_to_platform(
    platform: str,
    content: str,
    auth_token: str,
    proxies: Optional[Dict[str, str]] = None,
) -> str:
    """Publish ``content`` to a social platform.

    The function currently supports a small set of platforms.  It issues a
    ``POST`` request to the appropriate endpoint and returns the event or message
    identifier supplied by the remote service.

    Parameters
    ----------
    platform:
        Name of the platform (e.g. ``"twitter"`` or ``"reddit"``).
    content:
        The text content to publish.
    auth_token:
        The bearer token or API key required by the platform.

    Returns
    -------
    str
        Identifier for the created post as reported by the platform.
    """

    if requests is None:  # pragma: no cover - trivial in tests
        raise RuntimeError("The 'requests' library is required to post to platforms")

    endpoints = {
        "twitter": ("https://api.twitter.com/2/tweets", {"text": content}),
        "reddit": (
            "https://oauth.reddit.com/api/submit",
            {"kind": "self", "sr": "", "title": content[:40], "text": content},
        ),
        "discord": (
            # The caller must include the channel ID in the token or content; this
            # placeholder endpoint demonstrates the pattern only.
            "https://discord.com/api/v10/channels/CHANNEL_ID/messages",
            {"content": content},
        ),
    }

    platform_lower = platform.lower()
    if platform_lower not in endpoints:
        raise ValueError(f"Unsupported platform: {platform}")

    url, payload = endpoints[platform_lower]
    headers = {"Authorization": f"Bearer {auth_token}"}

    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=10, proxies=proxies
        )
        resp.raise_for_status()
        data = resp.json()
        # common id fields used by different platforms
        return (
            data.get("id")
            or data.get("data", {}).get("id")
            or data.get("post_id", "")
        )
    except Exception as exc:  # pragma: no cover - network may be unavailable
        raise RuntimeError(f"Posting to {platform} failed: {exc}") from exc


__all__ = [
    "call_gemini_api",
    "post_to_platform",
    "_load_platform_keys",
    "_load_proxy_for_agent",
]

