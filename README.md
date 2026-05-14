# RWMG — Resonance-Weighted Memory Graph

**Production-style Python for agent memory policy:** a deterministic **policy engine** (memory graph, execution traces, replay-oriented semantics) plus a **multi-agent simulation harness** (prompt construction, LLM calls, feedback normalization, persona and lifecycle tooling).

This repository is intended as a **portfolio-quality systems sample** for roles in applied AI, ML platform, and backend-heavy agent teams.

---

## Why this repo maps to $130k+ IC roles (mid-tier AI companies)

Recruiters and hiring managers usually scan for *breadth + rigor*. This codebase demonstrates:

| Area | What is implemented here |
|------|---------------------------|
| **Agent / LLM systems** | Prompt assembly, memory ranking and injection, sanitization hooks, optional Google **Gemini** integration (`rwmg.utils.api_wrappers`), graceful degradation when keys or network are unavailable. |
| **Policy & state** | `Engine` façade over `ResonanceWeightedMemoryGraph`; structured `RuntimeState`, `ProcessResult`, execution traces suitable for audit and replay. |
| **Storage & scale thinking** | Pluggable backends: **in-memory**, **file**, **MessagePack**, **Redis**; revision/conflict patterns; migrations scaffold. |
| **Reliability patterns** | Circuit breaker, throttled model provider, fleet orchestration hooks, telemetry probes. |
| **Testing & quality** | **`pytest`** suite (multi-phase tests including isolation, orchestration, invariants, optional performance gate behind `RWMG_BENCHMARK=1`). |
| **Determinism / MLOps mindset** | Documented environment pinning and trace semantics — see [`ENVIRONMENT_LOCK.md`](ENVIRONMENT_LOCK.md). |
| **Automation** | Optional **Playwright**-based Reddit account bootstrap script for local experimentation (`create_reddit_accounts.py`). |

Stack: **Python ≥3.10**, **NumPy**, **msgpack**, **filelock**, **redis** (optional **fakeredis** for tests). Package version **0.8.0** (`pyproject.toml`).

---

## Architecture at a glance

- **`rwmg` (core)** — `Engine`, `memory_loop`, `runtime_state`, `runtime_config`, `model_provider`, `storage`, `orchestration`, `circuit_breaker`, migrations, explainability utilities. The simulation harness lives beside the core and does not import into the minimal runtime surface (`rwmg.__init__` documents this split).
- **`rwmg.sim_runner`** — Epoch orchestration, agent manifest bootstrap, metrics, multi-agent controller.
- **`rwmg.lifecycle_manager`** — Daily ritual: memory context, prompts, model I/O, posting hooks, state updates.
- **`rwmg.feedback`** — Post logging, resonance-style feedback, memory injection pipeline (file-backed and test-friendly collectors).
- **`rwmg.persona_generator`**, **`rwmg.social`** — Persona and community-targeting helpers.
- **`tests/`** — Phase-organized regression and invariant tests.

Rough size: **~8k lines** of library Python under `rwmg/`, plus tests and scripts — enough depth to show sustained ownership, not a weekend script.

---

## Install

```bash
pip install -e .
# optional: run tests with Redis-backed code paths using fakeredis
pip install -e ".[test]"
```

---

## Run the test suite

```bash
pytest
```

Optional stricter performance check:

```bash
# Windows PowerShell
$env:RWMG_BENCHMARK = "1"; pytest

# Windows cmd
set RWMG_BENCHMARK=1 && pytest

# macOS / Linux
export RWMG_BENCHMARK=1 && pytest
```

---

## Basic simulation usage

1. **Configure** agents and platforms under `config/` (YAML). Place API material under `secrets/` as documented in code (e.g. `secrets/platform_keys.json` — never commit real keys).
2. **Create agents and manifest:**

   ```python
   from rwmg.sim_runner.sim_start import create_agents, populate_manifest

   agents = create_agents(1, {}, {"email_domain": "example.com"})
   populate_manifest(list(agents.keys()))
   ```

3. **Run an epoch:**

   ```python
   from pathlib import Path
   import json
   from rwmg.sim_runner.epoch_runner import run_epoch

   manifest_path = Path("agents/persona_manifest.json")
   agent_manifest = json.loads(manifest_path.read_text())
   run_epoch(agent_manifest, epoch_length=1)
   ```

Each epoch walks active agents through the daily ritual and logs metrics. Posting to external platforms is **opt-in** (tokens present) and wrapped defensively; feedback collectors support **deterministic file-based** fixtures for CI.

---

## Optional: Reddit account bootstrap (local)

For research or local integration experiments, `create_reddit_accounts.py` can drive browser-based signup using **Playwright** and a proxy list. Summary:

1. Install **Python 3.10+** and project deps; add `playwright`, `requests`, `python-dotenv` if not already present.
2. `playwright install`
3. Fill `input/agents/persona_manifest.json` (name, email, `secrets_path` per agent).
4. Fill `input/proxies.txt` — one `http://user:pass@host:port` per line, aligned with agent order.
5. Run `python create_reddit_accounts.py`. CAPTCHAs may require a short manual solve window.

Respect Reddit’s terms of service and applicable law; this path is for **local, accountable experimentation**, not unsupervised production automation.

---

## License

See `pyproject.toml` — proprietary unless you add or replace a LICENSE file.

---
