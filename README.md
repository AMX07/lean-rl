# Lean verifier RL environment

A Lean proof-verification environment for reinforcement learning, with
reproducible dependencies, training templates, evaluation/search scripts,
container setup, and tests.

The environment gives a model a Lean theorem, checks its submitted proof with
Lean, and returns rewards for RL or evaluation. It does not translate Python
programs into Lean or include a trained model or GPU trainer implementation.

## Setup and verify

```sh
uv sync --locked
bash scripts/setup_lean.sh
uv run --locked pytest -q
uv run --locked python examples/run_verifier.py
uv run --locked python scripts/check_environment.py --core
```

The small integration tests use Lean's `Init` library. For bundled training
and benchmark datasets, install Mathlib too (a larger download):

```sh
bash scripts/setup_lean.sh --with-mathlib
source scripts/lean_env.sh
uv run --locked python scripts/check_environment.py
```

Lean, REPL, and Mathlib target **4.28.0**. Toolchains and model outputs stay out
of Git. `uv.lock` pins Python dependencies, including legacy `verifiers==0.1.14`.

## Contents

| Component | Location |
|---|---|
| Every published Python module/dataset | `vendor/lean-verifier-env/` |
| Single, repair, tool, tactic-step, recursive modes | Package `env.py` |
| Training templates (tool and repair) | `configs/rl/` |
| Leaderboard and candidate search | `scripts/run_leaderboard.py`, `scripts/run_maxproof_lite.py` |
| Local checker setup and smoke test | `scripts/setup_lean.sh`, `scripts/check_environment.py` |
| CPU environment and recursive sandbox images | `Dockerfile`, `sandbox/` |
| Tests and CI | `tests/`, `.github/workflows/test.yml` |
| Original-versus-patched examples | `examples/` |

## Use

```python
from verifiers import load_environment

env = load_environment(
    "lean-verifier-env", mode="tool", dataset_name="lean-workbook",
    checker_backend="local", max_turns=4, lean_pool_size=2,
)
```

The local checker reads paths exported by `scripts/lean_env.sh`. Hosted AXLE
checking uses `AXLE_API_KEY` and `checker_backend="axle"`. Recursive sandbox
runs require your own published image and sandbox credentials.

- [Training and evaluation](docs/TRAINING.md)
- [Source provenance and completeness](docs/SOURCE_COMPLETENESS.md)
- [Patch limitations](vendor/lean-verifier-env/README.md)
- [Historical upstream README](docs/UPSTREAM_README.md) (some commands are stale)
- [Regression examples](examples/README.md)

## Validation boundary

Complete single/repair/tool/step rollouts with simulated model responses and
real Lean checks, tool retries, repair feedback, tactic completion,
recursive final scoring, source coverage, dataset isolation, script client setup,
and package builds are tested. Training TOMLs are schema-validated against Prime
RL v0.6.0. GPU training, live inference calls, hosted sandbox rollouts, and Docker
builds have not been run here. The Docker daemon was unavailable. Training
configs are starting templates, not evidence of a completed training run.

The patch accepts tactic bodies or exact standalone declarations; arbitrary
full Lean files with surrounding declarations are unsupported. This is not an
exhaustive verifier soundness audit.

Upstream author: **Pradheep**. Original declared license: MIT. Authorship and
license metadata are retained.
