"""MessagePack binary persistence for large memory stores."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import msgpack
from filelock import FileLock

from rwmg.storage.base import StorageBackend
from rwmg.storage.exceptions import StorageRevisionConflict

log = logging.getLogger("rwmg.storage")


class MsgPackStorageBackend(StorageBackend):
    """Same logical document as JSON, stored as ``memory_store.msgpack``."""

    def __init__(self, root_dir: Path | str, agent_id: str):
        self.root_dir = Path(root_dir)
        self.agent_id = agent_id
        self.agent_dir = self.root_dir / agent_id
        self.store_path = self.agent_dir / "memory_store.msgpack"
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
            raw = self.store_path.read_bytes()
            data = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("msgpack load failed (%s): %s", self.store_path, exc)
            return None
        return data if isinstance(data, dict) else None

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
        packed = msgpack.packb(store, use_bin_type=True)
        tmp_path = self.store_path.with_suffix(".msgpack.tmp")
        tmp_path.write_bytes(packed)
        tmp_path.replace(self.store_path)


__all__ = ["MsgPackStorageBackend"]
