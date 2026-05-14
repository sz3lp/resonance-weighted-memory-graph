"""Utilities for collecting post feedback from social platforms.

The real RWMG system would reach out to the respective platform APIs to
retrieve engagement metrics and raw comments for a post.  To keep the test
environment lightweight and deterministic this implementation falls back to
reading JSON files from ``post_url`` when available and otherwise returns an
empty data structure.  The goal is to normalise disparate feedback formats into
the canonical schema described in the project brief so that downstream modules
can attribute resonance scores.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Dict, List

try:  # pragma: no cover - import is trivial but may fail in minimal envs
    import requests
except Exception:  # pragma: no cover
    requests = None


_POSITIVE_WORDS = {
    "good",
    "great",
    "love",
    "excellent",
    "happy",
    "joy",
    "nice",
    "like",
}

_NEGATIVE_WORDS = {
    "bad",
    "terrible",
    "hate",
    "angry",
    "sad",
    "awful",
    "dislike",
}


def _basic_sentiment(text: str) -> float:
    """Compute a naive sentiment score in the range ``[-1, 1]``.

    The heuristic simply counts occurrences of words from small positive and
    negative vocabularies and normalises by the total number of words.  It is by
    no means a sophisticated sentiment analyser but suffices for the unit tests
    and keeps the project free from heavy dependencies.
    """

    tokens = re.findall(r"[A-Za-z']+", text.lower())
    if not tokens:
        return 0.0

    score = sum(1 for t in tokens if t in _POSITIVE_WORDS) - sum(
        1 for t in tokens if t in _NEGATIVE_WORDS
    )
    return max(-1.0, min(1.0, score / len(tokens)))


def _extract_tags(text: str) -> List[str]:
    """Derive a small set of keyword tags from ``text``.

    The implementation purposely remains extremely lightweight.  It simply
    returns the unique alphanumeric words longer than four characters which
    appear in the text.  The result is capped at five tags to keep subsequent
    memory entries concise.
    """

    tokens = re.findall(r"[A-Za-z']+", text.lower())
    tags = sorted({t for t in tokens if len(t) > 4})
    return tags[:5]


def _compute_comment_resonance(comment: Dict, sentiment: float) -> float:
    """Compute a naive resonance score for a single comment.

    The score combines the upvote count with the sentiment polarity.  It is not
    meant to be a perfect measure but provides a deterministic value in the
    ``[0, 1]`` range for tests and downstream weighting functions.
    """

    upvotes = float(comment.get("ups", comment.get("score", 0)))
    base = upvotes / (upvotes + 10.0)  # normalise vote influence
    resonance = base * (1.0 + (sentiment * 0.5))
    return max(0.0, min(1.0, resonance))


def _fetch_comments_from_api(post_id: str, platform: str) -> List[Dict]:
    """Fetch raw comments for ``post_id``.

    Similar to :func:`collect_feedback`, this helper first attempts to treat the
    ``post_id`` as a local JSON file path containing a ``"comments"`` array.  If
    the file is not present it falls back to an HTTP ``GET`` when the optional
    :mod:`requests` dependency is available.  Network failures simply yield an
    empty list.
    """

    path = Path(post_id)
    data: Dict = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
    elif requests is not None:  # pragma: no cover - network disabled in tests
        try:
            resp = requests.get(post_id, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            data = {}

    comments = data.get("comments", [])
    if not isinstance(comments, list):
        return []
    return comments


# feedback/resonance_collector.py
def collect_feedback(post_url: str, platform: str, agent_manifest: Dict) -> Dict:
    """Normalise feedback metrics for a previously logged post.

    Parameters
    ----------
    post_url:
        Location of the post.  For the purposes of the tests this may point to a
        local JSON file containing mock feedback data.
    platform:
        Name of the social platform the post was published on.  Currently only
        used for bookkeeping but retained for future extensibility.
    agent_manifest:
        Mapping of agent identifiers to their metadata.  Used to determine which
        comments originate from other agents so that the human/agent ratio can be
        computed.

    Returns
    -------
    Dict
        A dictionary following the ``feedback/feedback_data.json`` schema.
    """

    raw_data: Dict = {}
    path = Path(post_url)

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            raw_data = {}
    else:
        # Fallback to an HTTP GET; failures simply yield an empty dataset.
        if requests is not None:  # pragma: no cover - network disabled in tests
            try:
                resp = requests.get(post_url, timeout=5)
                resp.raise_for_status()
                raw_data = resp.json()
            except Exception:
                raw_data = {}

    likes = int(raw_data.get("likes", 0))
    shares = int(raw_data.get("shares", 0))
    comments: List[Dict] = raw_data.get("comments", []) or []

    agent_ids = set(agent_manifest.keys())
    agent_names = {
        v.get("name") for v in agent_manifest.values() if isinstance(v, dict)
    }

    human_comments = 0
    agent_engagement = 0
    sentiments: List[float] = []

    for comment in comments:
        text = str(comment.get("text", ""))
        author = str(comment.get("author", ""))
        sentiments.append(_basic_sentiment(text))
        if author in agent_ids or author in agent_names:
            agent_engagement += 1
        else:
            human_comments += 1

    total_replies = len(comments)
    human_ratio = human_comments / total_replies if total_replies else 0.0
    human_ratio = max(0.0, min(1.0, human_ratio))

    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
    avg_sentiment = max(-1.0, min(1.0, avg_sentiment))

    # Persist the raw feedback for traceability.  Failures are non-critical; an
    # empty reference path signals that the data could not be stored.
    feedback_dir = Path("feedback")
    feedback_dir.mkdir(exist_ok=True)
    raw_ref = ""
    try:
        ref_path = feedback_dir / f"raw_{uuid.uuid4().hex}.json"
        with ref_path.open("w", encoding="utf-8") as fh:
            json.dump(raw_data, fh, ensure_ascii=False, indent=2)
        raw_ref = str(ref_path)
    except OSError:
        raw_ref = ""

    return {
        "post_url": post_url,
        "platform": platform,
        "total_likes": likes,
        "total_shares": shares,
        "total_replies": total_replies,
        "human_comment_ratio": human_ratio,
        "average_sentiment_score": avg_sentiment,
        "agent_engagement_count": agent_engagement,
        "raw_feedback_ref": raw_ref,
    }


def collect_comment_feedback(agent_id: str, post_id: str, platform: str) -> List[Dict]:
    """Return processed feedback for individual comments on ``post_id``.

    Parameters
    ----------
    agent_id:
        Identifier of the agent that authored the original post.  Currently
        unused but reserved for future filtering of self-replies.
    post_id:
        Identifier or path of the post to retrieve comments for.  In the test
        environment this may be a local JSON file path.
    platform:
        Name of the platform the post was published on.  Included for
        completeness and added to the resulting ``origin`` field by the caller.
    """

    comments = _fetch_comments_from_api(post_id, platform)
    processed: List[Dict] = []
    for comment in comments:
        body = str(comment.get("body") or comment.get("text") or "")
        sentiment_score = _basic_sentiment(body)
        if sentiment_score > 0.2:
            sentiment_label = "positive"
        elif sentiment_score < -0.2:
            sentiment_label = "negative"
        else:
            sentiment_label = "neutral"

        tags = _extract_tags(body)
        resonance = _compute_comment_resonance(comment, sentiment_score)

        processed.append(
            {
                "comment_id": str(comment.get("id") or uuid.uuid4().hex),
                "timestamp": comment.get("created_utc"),
                "author": comment.get("author"),
                "sentiment": sentiment_label,
                "tags": tags,
                "resonance_score": resonance,
                "body": body,
            }
        )

    return processed

