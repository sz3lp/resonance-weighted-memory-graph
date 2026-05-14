"""Utilities for mimicking subreddit comment style."""

from __future__ import annotations

import requests
from typing import List


REDDIT_BASE_URL = "https://www.reddit.com"
USER_AGENT = "social-simulator/0.1"


def fetch_comment_style_examples(subreddit: str, limit: int = 5) -> List[str]:
    """Return high-scoring comments from ``subreddit``.

    Parameters
    ----------
    subreddit:
        Name of the subreddit to sample from.
    limit:
        Maximum number of comment examples to return.

    The function queries Reddit's public JSON endpoints without authentication.
    It gracefully falls back to an empty list if requests fail or the payload
    does not contain the expected structure.
    """

    if not subreddit:
        return []

    headers = {"User-Agent": USER_AGENT}
    examples: List[str] = []
    try:
        listing = requests.get(
            f"{REDDIT_BASE_URL}/r/{subreddit}/hot.json?limit={limit}",
            headers=headers,
            timeout=10,
        ).json()
        posts = listing.get("data", {}).get("children", [])
        for post in posts:
            post_id = post.get("data", {}).get("id")
            if not post_id:
                continue
            thread = requests.get(
                f"{REDDIT_BASE_URL}/r/{subreddit}/comments/{post_id}.json?sort=top&limit=1",
                headers=headers,
                timeout=10,
            ).json()
            comments = thread[1].get("data", {}).get("children", []) if len(thread) > 1 else []
            if not comments:
                continue
            body = comments[0].get("data", {}).get("body")
            if body:
                examples.append(body.strip())
            if len(examples) >= limit:
                break
    except Exception:
        return []
    return examples[:limit]


__all__ = ["fetch_comment_style_examples"]
