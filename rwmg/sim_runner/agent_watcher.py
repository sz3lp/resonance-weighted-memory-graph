"""Utilities for monitoring global and per-agent metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

try:  # Optional dependency used only for graph visualisation
    import networkx as nx  # type: ignore
except Exception:  # pragma: no cover - networkx may be missing
    nx = None  # type: ignore


def log_global_metrics(agent_manifest: Dict, epoch: int) -> None:
    """Append basic metrics about the simulation to a log file."""

    metrics = {"epoch": epoch, "active_agents": len(agent_manifest)}
    log_path = Path("staging") / "metrics.log"
    log_path.parent.mkdir(exist_ok=True, parents=True)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics) + "\n")
    except OSError:
        pass


def visualize_agent_graphs(agent_uuid: str) -> None:
    """Produce a lightweight visualisation of an agent's memory graph.

    If ``networkx`` is available, the graph is loaded from the ``.gexf`` file
    and basic statistics are written to ``temp/graph_stats.json``.  Missing
    dependencies or files simply result in the function returning quietly.
    """

    if nx is None:  # pragma: no cover - optional dependency
        return

    graph_path = Path("agents") / agent_uuid / "memory_graph.gexf"
    if not graph_path.exists():
        return

    try:
        g = nx.read_gexf(graph_path)  # type: ignore[arg-type]
    except Exception:
        return

    stats = {"nodes": g.number_of_nodes(), "edges": g.number_of_edges()}
    out_path = graph_path.parent / "temp" / "graph_stats.json"
    out_path.parent.mkdir(exist_ok=True)
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["log_global_metrics", "visualize_agent_graphs"]

