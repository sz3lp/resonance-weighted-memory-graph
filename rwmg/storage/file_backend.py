"""JSON file storage under ``root_dir / agent_id / memory_store.json``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from filelock import FileLock

from rwmg.storage.base import StorageBackend
from rwmg.storage.exceptions import StorageRevisionConflict

log = logging.getLogger("rwmg.storage")


class FileStorageBackend(StorageBackend):
    def __init__(self, root_dir: Path | str, agent_id: str):
        self.root_dir = Path(root_dir)
        self.agent_id = agent_id
        self.agent_dir = self.root_dir / agent_id
        self.store_path = self.agent_dir / "memory_store.json"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self._advisory = FileLock(str(self.agent_dir / ".rwmg_store.lock"))
        self._lock_held = False

    def lock(self, timeout: float = 30.0) -> bool:
        ok = self._advisory.acquire(timeout=float(timeout))
        self._lock_held = bool(ok)
        return bool(ok)

    def unlock(self) -> None:
        if self._lock_held:
            self._advisory.release()
            self._lock_held = False

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.store_path.is_file():
            return None
        try:
            with self.store_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed loading memory store (%s): %s", self.store_path, exc)
            return None
        return raw if isinstance(raw, dict) else None

    def save(self, store: Dict[str, Any]) -> None:
        disk = self.load()
        if disk is None:
            store["storage_revision"] = 1
        else:
            disk_rev = int(disk.get("storage_revision", 0))
            incoming = int(store.get("storage_revision", 0))
            if incoming != disk_rev:
                raise StorageRevisionConflict(incoming, disk_rev)
            store["storage_revision"] = disk_rev + 1
        tmp_path = self.store_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=True, indent=2, sort_keys=True)
        tmp_path.replace(self.store_path)


__all__ = ["FileStorageBackend"]
