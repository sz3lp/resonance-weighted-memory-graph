"""Prompt construction utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def build_prompt(
    agent_id: str,
    agent_state: Dict,
    memory_fragments: List[str],
    tone: str,
    platform_profile: Dict,
    style_examples: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Assemble a prompt for the agent's next post.

    The function is intentionally lightweight; it merely combines the supplied
    pieces into a textual instruction that can be sent to an LLM.  The second
    element of the returned tuple is the target platform name as provided in the
    ``platform_profile`` mapping under the ``"platform"`` key.
    """

    platform = platform_profile.get("platform", "")
    style = platform_profile.get("style", "")
    max_tokens = platform_profile.get("max_tokens", 280)

    if not memory_fragments:
        log_path = Path("agents") / agent_id / "memory_log.json"
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh) or []
        except (OSError, json.JSONDecodeError):
            log_entries = []
        memory_fragments = [
            entry.get("content", "")
            for entry in log_entries[-5:]
            if entry.get("content")
        ]

    memory_context = "\n".join(memory_fragments) if memory_fragments else ""
    style_block = "\n".join(style_examples or [])
    prompt = (
        f"You are agent {agent_id}. Write a {style} post for {platform} "
        f"in a {tone} tone. Stay within {max_tokens} characters."
    )
    if memory_context:
        prompt += f"\nConsider these memories:\n{memory_context}"
    if style_block:
        prompt += (
            "\nRespond to this post in the style of the following comments from the subreddit:\n"
            f"{style_block}"
        )

    return prompt, platform


__all__ = ["build_prompt"]

