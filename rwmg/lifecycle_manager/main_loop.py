"""Core daily routine executed for each active agent."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

import yaml

from rwmg.feedback.post_action_logger import log_agent_output

from rwmg.feedback.prompt_tuner import tune_prompt_parameters

from rwmg.lifecycle_manager.output_sanitizer import sanitize_output

from rwmg.lifecycle_manager.prompt_engine.inject_memory_context import (
    format_memory_context,
)
from rwmg.lifecycle_manager.prompt_engine.prompt_builder import build_prompt
from rwmg.lifecycle_manager.prompt_engine.prompt_logger import log_injected_memories
from rwmg.lifecycle_manager.prompt_engine.tone_selector import select_tone
from rwmg.social.community_discovery import choose_target_community
from rwmg.utils.api_wrappers import (
    _load_platform_keys,
    call_gemini_api,
    post_to_platform,
)
from rwmg.utils.memory_extractor import rank_memories
from rwmg.feedback.memory_injector import process_and_log_interactions


def _load_behavior_profiles() -> Dict[str, Dict]:
    config_path = Path("config") / "agent_behavior_profiles.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


BEHAVIOR_PROFILES = _load_behavior_profiles()


def log_violation(agent_id: str, result: Dict, content: str = "") -> None:
    """Persist a record of blocked content for offline review."""
    log_dir = Path("quarantine_log")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().isoformat().replace(":", "-")
    entry = {"agent_id": agent_id, "content": content, **result}
    file_path = log_dir / f"{agent_id}_{timestamp}.json"
    try:
        with file_path.open("w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def retry_prompt_with_constraints(agent_id: str, reason: List[str]) -> None:
    """Placeholder hook to re-run generation with additional constraints."""
    # In this reference implementation we simply record the failure and return.
    _ = (agent_id, reason)
    return


def _frequency_allows_post(freq: str) -> bool:
    if freq == "daily":
        return True
    if freq == "random":
        return random.random() < 0.5
    match = re.match(r"(\d+)x/week", str(freq))
    if match:
        count = int(match.group(1))
        return random.random() < count / 7
    match = re.match(r"(\d+)x/day", str(freq))
    if match:
        count = int(match.group(1))
        return random.random() < min(1.0, count)
    return True


def _within_activity_window(window: str, now: datetime) -> bool:
    hour = now.hour
    if window in (None, "all_day"):
        return True
    if window in ("morning", "fixed_morning"):
        return 6 <= hour < (9 if window == "fixed_morning" else 12)
    if window == "midday":
        return 11 <= hour < 14
    if window == "afternoon":
        return 12 <= hour < 17
    if window == "evening":
        return 17 <= hour < 22
    if window == "night":
        return hour >= 22 or hour < 6
    if window == "random":
        return random.random() < 0.5
    if window == "sporadic":
        return random.random() < 0.25
    return True


def _should_post(agent_state: Dict, profile: Dict, now: datetime) -> bool:
    next_time = agent_state.get("next_post_time")
    if next_time:
        try:
            if now < datetime.fromisoformat(str(next_time)):
                return False
        except ValueError:
            pass

    if not _within_activity_window(profile.get("activity_windows"), now):
        return False

    if not _frequency_allows_post(profile.get("post_frequency")):
        return False

    return True


def run_agent_day(agent_uuid: str, current_day: int, proxies: Optional[Dict[str, str]] = None) -> None:
    """Execute the posting and memory update cycle for ``agent_uuid``.

    The implementation is intentionally lightweight for the unit tests; it
    prepares a prompt from the agent's highest weighted memories, obtains model
    output and logs the result as a new memory event.
    """

    agent_dir = Path("agents") / agent_uuid
    state_path = agent_dir / "agent_state.json"
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            agent_state: Dict = json.load(fh)
    except (OSError, json.JSONDecodeError):
        agent_state = {}


    # Load platform profiles and choose the first available platform
    try:
        import yaml

        with open("config/platform_profiles.yaml", "r", encoding="utf-8") as fh:
            profiles = yaml.safe_load(fh) or {}
    except Exception:
        profiles = {}

    platform_name, profile = next(iter(profiles.items()), ("twitter", {}))
    profile = dict(profile or {})
    profile["platform"] = platform_name

    profile_name = agent_state.get("behavior_profile", "")
    behaviour = BEHAVIOR_PROFILES.get(profile_name, {})
    now = datetime.utcnow()
    if not _should_post(agent_state, behaviour, now):
        return


    # Update memory rankings to refresh cache files
    rank_memories(agent_uuid)

    cache_path = agent_dir / "memory_cache_top5.json"
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cache = []

    community = agent_state.get("target_subreddit") or profile.get("community")
    memory_context, style_examples = format_memory_context(cache, community)
    memory_fragments = [memory_context] if memory_context else []
    memory_ids = [entry.get("event_id") for entry in cache if entry.get("event_id")]
    memory_weights = [entry.get("content", "") for entry in cache]

    # Load persona metadata for community targeting
    try:
        persona_path = agent_dir / "persona_meta.yaml"
        with persona_path.open("r", encoding="utf-8") as fh:
            persona_meta = yaml.safe_load(fh) or {}
    except Exception:
        persona_meta = {}

    target_subreddit = choose_target_community(
        agent_uuid, persona_meta, memory_weights
    )

    # Tune state based on recent feedback before constructing the prompt
    memory_log_path = agent_dir / "memory_log.json"
    try:
        with memory_log_path.open("r", encoding="utf-8") as fh:
            memory_log = json.load(fh)
    except (OSError, json.JSONDecodeError):
        memory_log = []


    agent = tune_prompt_parameters(agent_uuid, memory_log, agent_state)
    try:
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(agent_state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    tone = select_tone(
        agent_state.get("emotional_vector", {}), profile.get("community_tone")
    )

    prompt, platform = build_prompt(
        agent_uuid, agent_state, memory_fragments, tone, profile, style_examples
    )


    tone = select_tone(agent_state.get("emotional_vector", {}))

    # Load platform profiles and choose the first available platform
    try:
        with open("config/platform_profiles.yaml", "r", encoding="utf-8") as fh:
            profiles = yaml.safe_load(fh) or {}
    except Exception:
        profiles = {}


    platform_name, profile = next(iter(profiles.items()), ("twitter", {}))
    profile = dict(profile or {})
    profile["platform"] = platform_name
    if target_subreddit:
        profile["target_subreddit"] = target_subreddit

    platform_name, platform_profile = next(iter(profiles.items()), ("twitter", {}))
    platform_profile = dict(platform_profile or {})
    platform_profile["platform"] = platform_name


    prompt, platform = build_prompt(
        agent_uuid,
        agent_state,
        memory_fragments,
        tone,
        platform_profile,
        style_examples,
    )

    try:
        raw_output = call_gemini_api(prompt, proxies=proxies)
    except Exception:
        raw_output = f"{agent_uuid[:8]} placeholder post"

    sanitization_result = sanitize_output(
        raw_output, {"agent_id": agent_uuid, "platform": platform}
    )
    if not sanitization_result["passed"]:
        log_violation(agent_uuid, sanitization_result, raw_output)
        retry_prompt_with_constraints(agent_uuid, sanitization_result["violations"])
        return

    event_id = log_agent_output(agent_uuid, raw_output, platform)
    log_injected_memories(agent_uuid, event_id, memory_ids)
    process_and_log_interactions(agent_uuid, event_id, platform)

    try:
        token_map = _load_platform_keys()
        auth_token = token_map.get(f"{platform}_token", "")
        if auth_token:
            post_to_platform(platform, raw_output, auth_token, proxies=proxies)
    except Exception:
        pass

    agent_state["last_post_timestamp"] = now.isoformat()
    cooldown = behaviour.get("cooldown_period_hours", [24, 24])
    try:
        min_cd, max_cd = int(cooldown[0]), int(cooldown[1])
    except Exception:
        min_cd, max_cd = 24, 24
    hours = random.randint(min_cd, max_cd)
    agent_state["next_post_time"] = (now + timedelta(hours=hours)).isoformat()

    try:
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(agent_state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["run_agent_day"]

