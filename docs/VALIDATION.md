# Validation recorded 2026-11-17

Version: `lean-verifier-env 0.10.0+local.2`, `verifiers 0.1.14`, Lean/REPL 4.28.0.

| Check | Result |
|---|---|
| `uv sync --locked` | Dependency lock resolves and installs |
| `uv run --locked pytest -q` | 23 passed; 2 dependency deprecation warnings |
| `uv run --locked python examples/run_verifier.py` | All 12 real-Lean regression cases passed |
| `scripts/check_environment.py --core` | Core reference proof verified |
| Complete single/repair/tool/step framework rollouts | Passed with simulated HTTP model responses and real Lean checks |
| Training TOMLs against Prime RL v0.6.0 `RLConfig` | Both passed full schema validation |
| Restored leaderboard/search script clients and help | Passed without live inference calls |
| Wheel and source distribution build | Passed; wheel includes every original package file |
| Published source archive coverage | All 38 source files represented |
| Shell syntax and Git whitespace checks | Passed |

No GPU optimizer run, live model-provider inference, AXLE API call, hosted RLM
sandbox rollout, full Mathlib dataset sweep, or Docker image build was performed.
The local Docker daemon was unavailable. These remain deployment checks; the
repository does not claim model-training results or production certification.

The full rollout tests are specifically not model-quality benchmarks: responses
are predetermined to exercise the interface, retries, final-turn scoring, and
tool feedback, while every proof verdict comes from actual Lean execution.
