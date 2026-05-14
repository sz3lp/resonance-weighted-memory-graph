#!/usr/bin/env python3
"""CLI: verify deterministic replay of a saved ``process`` trace."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from rwmg.utils.replay import replay_trace_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="Path to JSON trace (with initial_memory_store)")
    parser.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help="Working directory (default: temp dir, removed after success)",
    )
    parser.add_argument("--agent-id", type=str, default=None, help="Override agent id subdirectory")
    args = parser.parse_args()
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="rwmg_replay_"))
    try:
        replay_trace_file(args.trace, scratch_root=scratch, agent_id=args.agent_id)
    finally:
        if args.scratch is None:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)
    print("replay_trace: OK")


if __name__ == "__main__":
    main()
