"""In-process storage for tests and ephemeral agents."""

from __future__ import annotations

import copy
import threading
from typing import Any, Dict, Optional

from rwmg.storage.base import StorageBackend
from rwmg.storage.exceptions import StorageRevisionConflict


class InMemoryStorageBackend(StorageBackend):
    """Mutable deep-copied snapshots; thread-local sharing when one instance is reused."""

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        if initial is not None:
            self._snapshot: Optional[Dict[str, Any]] = copy.deepcopy(initial)
        else:
            self._snapshot = None
        self._lease = threading.Lock()
        self._data_lock = threading.Lock()

    def lock(self, timeout: float = 30.0) -> bool:
        if timeout <= 0:
            return self._lease.acquire(blocking=False)
        return self._lease.acquire(timeout=float(timeout))

    def unlock(self) -> None:
        try:
            self._lease.release()
        except RuntimeError:
            pass

    def load(self) -> Optional[Dict[str, Any]]:
        with self._data_lock:
            if self._snapshot is None:
                return None
            return copy.deepcopy(self._snapshot)

    def save(self, store: Dict[str, Any]) -> None:
        with self._data_lock:
            disk = self._snapshot
            if disk is None:
                store["storage_revision"] = 1
            else:
                disk_rev = int(disk.get("storage_revision", 0))
                incoming = int(store.get("storage_revision", 0))
                if incoming != disk_rev:
                    raise StorageRevisionConflict(incoming, disk_rev)
                store["storage_revision"] = disk_rev + 1
            self._snapshot = copy.deepcopy(store)


__all__ = ["InMemoryStorageBackend"]
