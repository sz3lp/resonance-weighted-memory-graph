"""Utility for deriving tags from memory content.

The function implemented here performs a very small scale natural language
processing task.  It relies solely on lightweight keyword matching so that it
works in constrained execution environments.  The goal is not to provide an
exhaustive taxonomy but to surface a reasonable set of thematic, emotional and
archetypal hints for subsequent scoring utilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# ``yaml`` is an optional dependency.  The project can operate without it and
# fall back to a small built‑in mapping.
try:  # pragma: no cover - exercised indirectly
    import yaml  # type: ignore
except Exception:  # ModuleNotFoundError or any other issue
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_archetype_keywords() -> Dict[str, List[str]]:
    """Load archetype specific keywords from ``config/archetype_rules.yaml``.

    When the configuration or the YAML parser is unavailable a default mapping
    mirroring the specification is used.  Only the ``keywords`` entries are
    relevant for tag extraction.
    """

    config_path = Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"

    # Fallback mapping used when the file or parser is missing
    fallback = {
        "king": ["sovereignty", "order", "duty", "responsibility", "legacy"],
        "lover": ["intimacy", "betrayal", "longing", "yearning", "vulnerability"],
        "warrior": ["discipline", "struggle", "victory", "force", "courage"],
        "magician": ["insight", "system", "pattern", "knowledge", "transformation"],
    }

    if yaml is None:
        return fallback

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return fallback

    keywords: Dict[str, List[str]] = {}
    for archetype, info in data.items():
        kws = [kw.lower() for kw in info.get("keywords", [])]
        keywords[archetype.lower()] = kws

    return keywords or fallback


ARCHETYPE_KEYWORDS = _load_archetype_keywords()

# Basic vocabulary for emotions reflected in ``agent_state.json``
EMOTION_KEYWORDS: Dict[str, List[str]] = {
    "joy": ["joy", "happy", "delight", "smile", "glad"],
    "anger": ["anger", "angry", "rage", "mad", "furious"],
    "grief": ["grief", "sad", "sorrow", "melancholy", "loss"],
    "contempt": ["contempt", "scorn", "disdain"],
    "affinity": ["love", "affection", "fond", "like", "care"],
    "stress": ["stress", "anxiety", "worried", "tense", "fear"],
}

# A small thematic vocabulary capturing common narrative motifs
THEME_KEYWORDS: Dict[str, List[str]] = {
    "childhood": ["childhood", "child", "kid", "school", "parent", "mother", "father"],
    "adolescence": ["adolescence", "teen", "teenage", "high school"],
    "abandonment": ["abandon", "abandoned", "left me", "deserted"],
    "attachment": ["attachment", "attach", "bond", "cling"],
    "peer_rejection": ["rejected", "rejection", "bully", "ostracised", "peer"],
    "humiliation": ["humiliat", "embarrass", "shame"],
    "vulnerability": ["vulnerab", "fragile", "weakness"],
    "betrayal": ["betray", "treachery", "backstab", "deceived"],
    "injury": ["injury", "wound", "scar", "hurt"],
    "success": ["success", "victory", "accomplish", "win"],
    "failure": ["fail", "failure", "lost", "lose"],
}

NEGATIVE_EMOTIONS = {"anger", "grief", "contempt", "stress"}


def extract_memory_tags(memory_content: str, agent_archetype: str) -> List[str]:
    """Extract thematic, emotional and archetypal tags from ``memory_content``.

    Parameters
    ----------
    memory_content:
        Raw textual description of the memory.
    agent_archetype:
        Core archetype of the agent (e.g. ``"King"``).

    Returns
    -------
    list[str]
        Sorted list of unique tags.  If no tags can be derived a single
        ``"misc"`` tag is returned as a fallback.
    """

    text = memory_content.lower()
    tags: set[str] = set()

    # --- emotion tags -----------------------------------------------------
    for tag, keywords in EMOTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    # --- thematic tags ----------------------------------------------------
    for tag, keywords in THEME_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    # --- archetypal tag ---------------------------------------------------
    archetype_key = (agent_archetype or "").lower()
    if archetype_key:
        keywords = ARCHETYPE_KEYWORDS.get(archetype_key, [])
        if any(kw in text for kw in keywords):
            # Shadow vs. core is determined heuristically by emotion polarity
            if tags & NEGATIVE_EMOTIONS:
                tags.add(f"{archetype_key}_shadow")
            else:
                tags.add(archetype_key)
        else:
            # Include the archetype tag regardless to retain context
            tags.add(archetype_key)

    if not tags:
        return ["misc"]

    return sorted(tags)

