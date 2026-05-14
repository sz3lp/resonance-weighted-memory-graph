"""Simulate social interactions such as upvotes and downvotes.

The wider RWMG experiment models online communities where agents react to
content created by their peers.  This module provides a minimal implementation
of that behaviour: given a piece of text and an agent identifier it determines
whether the agent would upvote, downvote or ignore the post.  The decision is
based on the agent's stored memories which already encode resonance and
suppression information.

The rules implemented here are intentionally lightweight:

* If the post shares vocabulary with one of the agent's *top memories* it is
  considered aligned and will receive an **upvote**.
* If vocabulary overlaps with any entry in the agent's *suppression log* the
  post is considered discordant and will be **downvoted**.
* If neither condition holds the agent remains neutral and takes no action.

The goal is not to perfectly model social behaviour but to provide a deterministic
mechanism that other components – particularly the multi‑agent controller – can
leverage when simulating social feedback in a test environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from rwmg.utils.memory_extractor import rank_memories


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _load_texts(path: Path, key: str) -> List[str]:
    """Return a list of texts stored under ``key`` in ``path``.

    Missing files or malformed content result in an empty list; this keeps the
    caller's logic straightforward and mirrors the defensive style used
    throughout the code base.
    """

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return [str(item.get(key, "")) for item in data if item.get(key)]
        return [str(item) for item in data if item]
    return []


def _tokenise(texts: Sequence[str]) -> set:
    """Tokenise a sequence of texts into a case‑insensitive word set."""

    tokens = set()
    for text in texts:
        tokens.update(text.lower().split())
    return tokens


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_vote(agent_uuid: str, post_content: str) -> str:
    """Determine whether ``agent_uuid`` would upvote or downvote ``post_content``.

    The function refreshes the agent's memory rankings to ensure caches are up
    to date, then compares the vocabulary of the ``post_content`` against the
    agent's high‑resonance memories and suppression log.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent performing the evaluation.
    post_content:
        Text content of the post under consideration.

    Returns
    -------
    str
        One of ``"upvote"``, ``"downvote"`` or ``"neutral"``.
    """

    if not post_content:
        return "neutral"

    # Ensure memory caches are refreshed before reading them
    try:
        rank_memories(agent_uuid)
    except Exception:
        pass

    agent_dir = Path("agents") / agent_uuid
    top_memories = _load_texts(agent_dir / "memory_cache_top5.json", "content")
    bottom_memories = _load_texts(agent_dir / "suppression_log.json", "content")

    post_tokens = set(post_content.lower().split())
    top_tokens = _tokenise(top_memories)
    bottom_tokens = _tokenise(bottom_memories)

    if post_tokens & top_tokens:
        return "upvote"
    if post_tokens & bottom_tokens:
        return "downvote"
    return "neutral"


def apply_votes(agent_uuid: str, posts: Iterable[str]) -> List[Tuple[str, str]]:
    """Return vote actions for ``posts`` made by ``agent_uuid``.

    Each element of ``posts`` is analysed via :func:`evaluate_vote`.  A list of
    tuples ``(post, action)`` is returned for every post that results in an
    ``"upvote"`` or ``"downvote"``.  Neutral outcomes are omitted to keep the
    output concise for downstream consumers.
    """

    results: List[Tuple[str, str]] = []
    for post in posts:
        action = evaluate_vote(agent_uuid, post)
        if action != "neutral":
            results.append((post, action))
    return results


__all__ = ["evaluate_vote", "apply_votes"]
