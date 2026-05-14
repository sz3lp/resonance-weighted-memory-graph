# Environment lock (replay / cross-machine determinism)

Determinism in RWMG depends on **identical code, data, and numeric environment**, not only seeds.

## Runtime and build

- **Pin Python** in production and CI to the same `requires-python` range as `pyproject.toml` (fixed minor version recommended).
- **Pin dependencies**: install from a **locked** resolution (`pip-compile`, `uv lock`, or `poetry lock`) and deploy with that lockfile.
- **Threaded BLAS / OMP**: for bit-for-bit or lowest-variance replays, set  
  `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`  
  when running tests or offline replay.

## Semantics the core records

Each process trace includes: `global_seed`, `episode_seed`, `episode_index`, `memory_hash`, `embedding_version`, `python_version`, `config_hash` (via `trace_config_fingerprint`), and `git_hash` where available. Replay hosts must align **initial memory store**, **config fingerprint**, and **graph knobs** (production mode, clock, max memories, etc.) with the recorded trace.

## Forward compatibility

On-disk store **unknown top-level keys** are **logged as warnings** and are not hard failures, so newer clients can coexist during rollout.
