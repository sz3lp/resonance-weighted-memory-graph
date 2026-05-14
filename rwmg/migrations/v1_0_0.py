"""Phase 7 baseline: canonical ``memory_store`` shape with ``schema_version`` ``1.0.0``.

Legacy stores without ``schema_version`` are upgraded in-place by
``rwmg.memory_loop._migrate_legacy_memory_store`` before ``validate_schema`` runs.
"""

MEMORY_STORE_VERSION = "1.0.0"
