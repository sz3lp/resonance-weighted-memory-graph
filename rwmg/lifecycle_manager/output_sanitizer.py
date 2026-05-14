"""Utilities for validating LLM output before publication."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional


_TOXIC_PATTERNS = [
    r"\bhate\b",
    r"\bkill\b",
    r"\bidiot\b",
]


def _check_toxicity(content: str) -> List[str]:
    violations: List[str] = []
    for pattern in _TOXIC_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            word = pattern.strip("\\b")
            violations.append(f"Contains disallowed term: {word}")
    return violations


def _check_factuality(content: str) -> List[str]:
    violations: List[str] = []
    for match in re.findall(r"\b(\d{4})\b", content):
        year = int(match)
        if year < 1900 or year > 2100:
            violations.append(f"Suspicious year: {year}")
    misinfo_phrases = ["earth is flat", "moon is made of cheese", "2+2=5"]
    lower = content.lower()
    for phrase in misinfo_phrases:
        if phrase in lower:
            violations.append(f"Factual error detected: '{phrase}'")
    return violations


def _load_previous_posts(agent_id: str) -> List[str]:
    agent_dir = Path("agents") / agent_id
    log_path = agent_dir / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        return [str(entry.get("content", "")) for entry in entries if isinstance(entry, dict)]
    except Exception:
        return []


def _check_plagiarism(content: str, context: Dict[str, str]) -> List[str]:
    agent_id = context.get("agent_id") if context else None
    if not agent_id:
        return []
    violations: List[str] = []
    for previous in _load_previous_posts(agent_id):
        if not previous:
            continue
        similarity = SequenceMatcher(None, previous, content).ratio()
        if similarity > 0.9:
            violations.append("Content too similar to previous post")
            break
    return violations


def sanitize_output(content: str, context: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Run safety, factuality and originality checks on ``content``."""
    result = {"passed": True, "violations": [], "suggestion": None}
    context = context or {}

    violations: List[str] = []
    violations.extend(_check_toxicity(content))
    violations.extend(_check_factuality(content))
    violations.extend(_check_plagiarism(content, context))

    if violations:
        result["passed"] = False
        result["violations"] = violations

    return result


__all__ = ["sanitize_output"]

