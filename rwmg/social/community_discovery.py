"""Utilities for discovering and prioritizing Reddit communities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import requests


def _extract_keywords(persona_meta: Dict, memory_weights: List[str]) -> List[str]:
    """Collect keyword tokens from persona metadata and memory contents."""
    keywords: List[str] = []

    meta_sources = [
        persona_meta.get("archetype"),
        persona_meta.get("mood"),
        persona_meta.get("description"),
        persona_meta.get("persona_description"),
        persona_meta.get("bio"),
    ]
    interests = persona_meta.get("interests")
    if isinstance(interests, list):
        meta_sources.extend(interests)
    elif isinstance(interests, str):
        meta_sources.append(interests)

    for src in meta_sources:
        if isinstance(src, str):
            keywords.extend(re.findall(r"\w+", src.lower()))

    for mem in memory_weights or []:
        if isinstance(mem, str):
            keywords.extend(re.findall(r"\w+", mem.lower()))

    # deduplicate while preserving order
    deduped: List[str] = []
    seen = set()
    for word in keywords:
        if word not in seen:
            deduped.append(word)
            seen.add(word)
    return deduped


def discover_subreddits(agent_id: str, persona_meta: Dict, memory_weights: List[str]) -> List[Dict[str, float]]:
    """Return candidate subreddits with relevance scores."""
    keywords = _extract_keywords(persona_meta, memory_weights)
    scores: Dict[str, float] = {}
    for kw in keywords:
        if not kw:
            continue
        try:
            resp = requests.get(
                "https://www.reddit.com/subreddits/search.json",
                params={"q": kw, "limit": 5},
                headers={"User-Agent": f"rwm-agent-{agent_id}"},
                timeout=5,
            )
            data = resp.json().get("data", {})
            for child in data.get("children", []):
                info = child.get("data", {})
                name = info.get("display_name")
                if not name:
                    continue
                subs = float(info.get("subscribers", 0) or 0)
                active = float(info.get("active_user_count", 0) or 0)
                relevance = 1.0 + subs / 1_000_000 + active / 100_000
                prev = scores.get(name, 0.0)
                scores[name] = max(prev, relevance)
        except Exception:
            continue

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [{"subreddit": name, "score": score} for name, score in ordered]


def update_interest_graph(agent_id: str, subreddit: str, resonance_score: float) -> None:
    """Update the agent's interest graph with new interaction data."""
    path = Path("agents") / agent_id / "interest_graph.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            graph = json.load(fh)
    except (OSError, json.JSONDecodeError):
        graph = {}

    node = graph.get(subreddit, {"interactions": 0, "avg_resonance": 0.0})
    interactions = int(node.get("interactions", 0)) + 1
    avg = float(node.get("avg_resonance", 0.0))
    avg = (avg * (interactions - 1) + float(resonance_score)) / interactions
    node.update({"interactions": interactions, "avg_resonance": avg})
    graph[subreddit] = node

    # Apply decay to other subreddits
    for name, stats in graph.items():
        if name == subreddit:
            continue
        stats["avg_resonance"] = float(stats.get("avg_resonance", 0.0)) * 0.95

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(graph, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def choose_target_community(agent_id: str, persona_meta: Dict, memory_weights: List[str]) -> str:
    """Select the most promising subreddit for the agent's next action."""
    candidates = discover_subreddits(agent_id, persona_meta, memory_weights)

    blacklist: List[str] = []
    blacklist_path = Path("config") / "subreddit_blacklist.json"
    try:
        with blacklist_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, list):
                blacklist = [s.lower() for s in data]
    except (OSError, json.JSONDecodeError):
        blacklist = []

    filtered = [c for c in candidates if c["subreddit"].lower() not in blacklist]
    if not filtered:
        return ""

    ig_path = Path("agents") / agent_id / "interest_graph.json"
    try:
        with ig_path.open("r", encoding="utf-8") as fh:
            interest_graph = json.load(fh)
    except (OSError, json.JSONDecodeError):
        interest_graph = {}

    def combined_score(entry: Dict[str, float]) -> float:
        base = entry.get("score", 0.0)
        bonus = float(interest_graph.get(entry["subreddit"], {}).get("avg_resonance", 0.0))
        return base + bonus

    best = max(filtered, key=combined_score, default=None)
    return best["subreddit"] if best else ""


__all__ = [
    "discover_subreddits",
    "update_interest_graph",
    "choose_target_community",
]
