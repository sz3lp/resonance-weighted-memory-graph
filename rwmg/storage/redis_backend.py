"""Redis-backed storage with optimistic revision and distributed locking."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import msgpack

from rwmg.storage.base import StorageBackend
from rwmg.storage.exceptions import StorageRevisionConflict

log = logging.getLogger("rwmg.storage")

try:
    import redis
    from redis.exceptions import WatchError
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]
    WatchError = type("WatchError", (Exception,), {})  # type: ignore[misc, assignment]


class RedisStorageBackend(StorageBackend):
    """MsgPack-encoded store document under a single Redis key per agent."""

    def __init__(
        self,
        client: Any,
        agent_id: str,
        *,
        key_prefix: str = "rwmg",
    ) -> None:
        if redis is None:  # pragma: no cover
            raise RuntimeError("redis-py is required for RedisStorageBackend; pip install redis")
        self._r = client
        self.agent_id = agent_id
        self._key = f"{key_prefix}:v1:store:{agent_id}"
        self._lock_key = f"{key_prefix}:v1:lock:{agent_id}"
        self._held_lock: Optional[Any] = None

    def lock(self, timeout: float = 30.0) -> bool:
        lock = self._r.lock(
            self._lock_key,
            timeout=int(max(1, timeout)),
            blocking_timeout=float(timeout),
        )
        ok = bool(lock.acquire(blocking=True, blocking_timeout=float(timeout)))
        if ok:
            self._held_lock = lock
        return ok

    def unlock(self) -> None:
        lock = self._held_lock
        self._held_lock = None
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001 — best-effort unlock
                pass

    def load(self) -> Optional[Dict[str, Any]]:
        raw = self._r.get(self._key)
        if raw is None:
            return None
        try:
            data = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        except (ValueError, TypeError, OSError) as exc:
            log.warning("redis load unpack failed (%s): %s", self._key, exc)
            return None
        return data if isinstance(data, dict) else None

    def save(self, store: Dict[str, Any]) -> None:
        max_retries = 64
        for _ in range(max_retries):
            pipe = self._r.pipeline()
            try:
                pipe.watch(self._key)
                raw = pipe.get(self._key)
                if raw is None:
                    next_rev = 1
                else:
                    disk = msgpack.unpackb(raw, raw=False, strict_map_key=False)
                    if not isinstance(disk, dict):
                        pipe.unwatch()
                        raise StorageRevisionConflict(-1, -1)
                    disk_rev = int(disk.get("storage_revision", 0))
                    incoming = int(store.get("storage_revision", 0))
                    if incoming != disk_rev:
                        pipe.unwatch()
                        raise StorageRevisionConflict(incoming, disk_rev)
                    next_rev = disk_rev + 1
                payload = dict(store)
                payload["storage_revision"] = next_rev
                packed = msgpack.packb(payload, use_bin_type=True)
                pipe.multi()
                pipe.set(self._key, packed)
                pipe.execute()
                store["storage_revision"] = next_rev
                return
            except WatchError:
                continue
        raise RuntimeError(f"redis save failed after {max_retries} watch retries")


__all__ = ["RedisStorageBackend"]
