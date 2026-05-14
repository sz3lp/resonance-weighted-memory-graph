"""Helpers for injecting memory context into prompts.

The real system crafts a natural language summary of the most salient memories
to provide situational awareness to the language model.  For testing we employ a
simple formatter that joins memory snippets into a bullet list.
"""

from __future__ import annotations

from typing import Iterable, List, Dict, Optional, Tuple

from .comment_style_mimicry import fetch_comment_style_examples


def format_memory_context(
    memory_cache: Dict, subreddit: Optional[str] = None
) -> Tuple[str, List[str]]:
    """Return a natural language block and style examples.

    Parameters
    ----------
    memory_cache:
        Expected to be a sequence of mappings with a ``"content"`` field as
        produced by :func:`utils.memory_extractor.rank_memories`.
    subreddit:
        If provided, top comments from this subreddit will be retrieved and
        appended as style examples for prompt conditioning.
    """

    if not memory_cache and not subreddit:
        return "", []

    fragments: Iterable[str] = [
        entry.get("content", "") for entry in memory_cache if entry.get("content")
    ]
    lines: List[str] = [f"- {frag}" for frag in fragments if frag]

    examples: List[str] = []
    if subreddit:
        examples = fetch_comment_style_examples(subreddit)
        if examples:
            lines.append("Community style examples:")
            lines.extend([f"- {ex}" for ex in examples])

    return "\n".join(lines), examples


__all__ = ["format_memory_context"]

