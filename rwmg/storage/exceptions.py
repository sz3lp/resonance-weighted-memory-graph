"""Storage backend errors for optimistic concurrency and locking."""


class StorageRevisionConflict(Exception):
    """Raised when a save would overwrite newer remote state (first-writer-wins)."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"storage revision mismatch: client has {expected_revision}, "
            f"persisted {actual_revision}"
        )


class LockAcquireFailed(Exception):
    """Raised when ``StorageBackend.lock`` cannot be acquired within ``timeout``."""

    def __init__(self, message: str = "could not acquire storage lock") -> None:
        super().__init__(message)


__all__ = ["LockAcquireFailed", "StorageRevisionConflict"]
