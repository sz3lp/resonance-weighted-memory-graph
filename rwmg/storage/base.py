"""Abstract storage backends for resonance-weighted memory state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class StorageBackend(ABC):
    """Persist and load the canonical memory store document (single JSON-compatible dict)."""

    @abstractmethod
    def load(self) -> Optional[Dict[str, Any]]:
        """Return the raw store dict, or ``None`` if missing (fresh agent)."""

    @abstractmethod
    def save(self, store: Dict[str, Any]) -> None:
        """Atomically replace the persisted document with ``store``.

        Implementations that support optimistic concurrency compare the caller's
        ``storage_revision`` with the persisted revision and raise
        ``StorageRevisionConflict`` when stale (first-writer-wins).
        """

    def lock(self, timeout: float = 30.0) -> bool:
        """Acquire an exclusive advisory lock for this agent's store (optional).

        Returns ``True`` if the lock is held by this instance. Returns ``False``
        if ``timeout`` elapses without acquiring (non-blocking failure).
        """

        return True

    def unlock(self) -> None:
        """Release the lock acquired via :meth:`lock`."""

        return None


__all__ = ["StorageBackend"]

