"""Deterministic trace replay for the resonance-weighted memory graph."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from rwmg.memory_loop import ResonanceWeightedMemoryGraph, memory_store_hash, validate_schema


def load_trace(path: Path | str) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_store_snapshot(root_dir: Path, agent_id: str, store: Dict[str, Any]) -> None:
    validate_schema(store)
    agent_dir = root_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    dest = agent_dir / "memory_store.json"
    tmp = dest.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=True, indent=2, sort_keys=True)
    tmp.replace(dest)


def verify_replay(
    trace: Dict[str, Any],
    *,
    scratch_root: Path,
    agent_id: str,
    production_mode: bool = True,
    deterministic_clock: bool = True,
    max_memory_entries: Optional[int] = None,
    fingerprint: str = "",
    check_memory_hash: bool = False,
) -> None:
    """Restore ``initial_memory_store``, run ``process(trace['input'])``, assert output and policy."""

    initial = trace.get("initial_memory_store")
    if not isinstance(initial, dict):
        raise ValueError("trace missing required 'initial_memory_store' object")
    if scratch_root.exists():
        shutil.rmtree(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    _write_store_snapshot(scratch_root, agent_id, initial)
    loop = ResonanceWeightedMemoryGraph(
        agent_id=agent_id,
        root_dir=scratch_root,
        production_mode=production_mode,
        deterministic_clock=deterministic_clock,
        max_memory_entries=max_memory_entries,
    )
    output = loop.process(str(trace["input"]))
    expected_out = trace.get("output")
    if output != expected_out:
        raise AssertionError(f"replay output mismatch:\n got: {output!r}\n exp: {expected_out!r}")
    if check_memory_hash and trace.get("memory_hash"):
        h = memory_store_hash(loop.store.load_store())
        if h != trace["memory_hash"]:
            raise AssertionError(f"replay memory_hash mismatch: got {h}, expected {trace['memory_hash']}")
    policy = loop.store.load_policy_state()
    expected_policy = trace.get("policy_state_snapshot")
    if expected_policy is not None and json.dumps(policy, sort_keys=True) != json.dumps(
        expected_policy, sort_keys=True
    ):
        raise AssertionError("replay policy_state_snapshot mismatch")


def replay_trace_file(
    trace_path: Path | str,
    *,
    scratch_root: Path,
    agent_id: Optional[str] = None,
) -> None:
    trace = load_trace(trace_path)
    aid = agent_id or str(trace.get("agent_id") or trace.get("initial_memory_store", {}).get("agent_id", "default"))
    verify_replay(trace, scratch_root=scratch_root, agent_id=aid)
