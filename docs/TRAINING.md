# Training and evaluation

This repository supplies tasks, interaction loops, verifier rewards, datasets,
and evaluation/search tools. Model updates are performed by an external trainer.
GPU training needs Linux/CUDA compute, a model/inference service, and CPU/RAM
for Lean. The package is pinned to `verifiers==0.1.14`; current Prime RL main
uses a newer API, so compatibility must not be assumed.

## Training templates

`configs/rl/lean_agentic_local.toml` and `lean_repair_local.toml` use the official
[Prime RL v0.6.0 schema](https://github.com/PrimeIntellect-ai/prime-rl/tree/v0.6.0).
Both passed that release's `RLConfig.model_validate`; their environment arguments
also instantiate this package. These checks do not establish end-to-end trainer
interoperability. Templates train on Lean Workbook using groups of four rollouts
and LoRA. They load the local `lean-verifier-env` ID, not the unpatched Hub ID.

On a GPU trainer deployment:

1. Set up Prime RL v0.6.0 following its own installation instructions.
2. Install this package into the environment-worker Python environment with
   `uv pip install --python <worker-python> -e <checkout>/vendor/lean-verifier-env`.
   Reconcile the trainer's own verifiers pin first; do not silently replace it.
3. Install Lean/Mathlib on the workers. Export `LEAN_REPL_CMD`, `LEAN_PROJECT_DIR`,
   and PATH using `scripts/lean_env.sh` or equivalent container paths.
4. Run `scripts/check_environment.py` on the worker. Run
   `uv run python <checkout>/scripts/validate_training_configs.py` in the trainer
   environment, then pass the selected TOML to its `rl` entrypoint.
5. Start with one rollout batch and one optimizer update, checking that the
   loaded module path points to this patched package. Size workers/pools for RAM.

No GPU training run has been performed here. The next deployment-specific check
is a complete rollout batch and optimizer update on the selected trainer.

## Model evaluation and search

After setup, set your inference provider key securely before making live calls:

```sh
source scripts/lean_env.sh
uv run --locked python scripts/run_leaderboard.py \
  --provider openrouter --models <model-id> --datasets minif2f-test \
  --num-examples 5 --rollouts-per-example 2 --backend local
uv run --locked python scripts/run_maxproof_lite.py \
  --provider openrouter --model <model-id> --dataset toy \
  --max-tasks 1 --num-candidates 2 --rewrite-rounds 1 --backend local
```

Use provider-supported model identifiers. Scripts also accept Prime, OpenAI,
or an OpenAI-compatible base URL. MiniF2F test, ProofNet, and Putnam remain
evaluation-only; their loader requires explicit evaluation opt-in.

## Containers and recursive mode

```sh
docker build -t lean-rlm-sandbox:local sandbox
docker build -t lean-rl-env:local .
docker run --rm lean-rl-env:local
```

The environment image verifies the toy reference proofs by default. Both images
use Lean/Mathlib 4.28.0. Publish the sandbox to your own accessible registry and
set `LEAN_RLM_SANDBOX_IMAGE` for recursive mode. Supply sandbox and model-provider
credentials separately and configure the final scoring backend (local or AXLE).

The sandbox CLI gives process feedback; the shared final scorer determines
reward for the original theorem. Docker builds and hosted RLM execution remain
untested here because the Docker daemon/hosted services were unavailable. The
local RLM final-scoring function is covered by real-Lean tests.
