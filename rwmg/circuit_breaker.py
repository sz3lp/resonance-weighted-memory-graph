"""Lightweight circuit breaker for policy modules (transient vs sustained failure storms)."""

from __future__ import annotations

from typing import Dict, Optional


class PolicyCircuitBreaker:
    """After ``failure_threshold`` consecutive failures, bypass the module until cooldown elapses."""

    def __init__(self, failure_threshold: int = 5, cooldown_episodes: int = 8) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_episodes = max(1, cooldown_episodes)
        self._failures: Dict[str, int] = {}
        self._open_until_episode: Dict[str, int] = {}

    def is_open(self, module: str, episode: int) -> bool:
        until = self._open_until_episode.get(module)
        if until is not None and episode < until:
            return True
        if until is not None and episode >= until:
            self._failures[module] = 0
            self._open_until_episode.pop(module, None)
        return False

    def success(self, module: str) -> None:
        self._failures[module] = 0

    def failure(self, module: str, episode: int) -> None:
        self._failures[module] = self._failures.get(module, 0) + 1
        if self._failures[module] >= self.failure_threshold:
            self._open_until_episode[module] = episode + self.cooldown_episodes


__all__ = ["PolicyCircuitBreaker"]
