# lean-verifier-env

Lean 4 theorem-proving environment for Prime Intellect / `verifiers`, built around a
strict verifier reward. The model receives a theorem ending in `:= by`; the environment
checks the model's proof with either hosted AXLE or a local Lean REPL pool. Reward is
`1.0` only when the submitted proof verifies.

### Overview

- **Environment ID**: `pradheep/lean-verifier-env`
- **Version**: `0.10.0`
- **Tags**: lean, theorem-proving, verification, axle, eval, train, rlm
- **Primary use**: verifier-backed Lean RLM/RL experiments with a controlled action
  space.
- **Not the same niche as `opencode-lean`**: `opencode-lean` is a full sandboxed
  OpenCode agent environment with bash/edit tools. This env is a verifier-first reward
  lab: smaller action space, easier reward audits, and cheaper local iteration.

### Quickstart

```bash
# from the repo root
uv run --project environments/lean-verifier-env --extra dev python -m pytest tests
uv run --project environments/lean-verifier-env vf-eval lean-verifier-env -n 5 -r 1
```

For local Lean checking:

```bash
export LEAN_CHECKER_BACKEND=local
export LEAN_REPL_CMD=/path/to/repl/.lake/build/bin/repl
export LEAN_PROJECT_DIR=/path/to/mathlib-project
export LEAN_POOL_SIZE=2
```

For hosted AXLE checking:

```bash
export AXLE_API_KEY=your-api-key
export LEAN_CHECKER_BACKEND=axle
```

#### Inference providers

The env is provider-agnostic — the policy model is supplied by the harness, so
pick any provider with `prime eval run -p <provider>`:

```bash
# Prime Intellect inference (uses your `prime login` account; no extra key needed)
# Model ids come from the Prime Inference catalog (GET api.pinference.ai/api/v1/models).
prime eval run pradheep/lean-verifier-env -m openai/gpt-oss-120b -p prime
# ...or pin models to Prime Inference via the shipped registry:
prime eval run pradheep/lean-verifier-env -m openai/gpt-oss-120b -e configs/endpoints.toml

# OpenRouter (needs OPENROUTER_API_KEY)
prime eval run pradheep/lean-verifier-env -m <model> -p openrouter
```

`-p prime` resolves `https://api.pinference.ai/api/v1` + `PRIME_API_KEY`
(falling back to `~/.prime/config.json`); `-p openrouter` needs
`OPENROUTER_API_KEY`. The in-process leaderboard takes the same choice via
`--provider {prime,openrouter,openai}` (see Development).

### Architecture

The package exposes `load_environment` and supports five interaction modes:

| Mode | Env | Behavior | Best use |
|------|-----|----------|----------|
| `single` | `SingleTurnEnv` | Model submits one proof; env verifies once | evals, dataset checks, cheap baselines |
| `repair` | `LeanRepairEnv` | Env returns Lean diagnostics after failed attempts | multi-turn proof repair experiments |
| `tool` / `agentic` | `LeanToolEnv` | Model calls `lean_check(proof)` during rollout | tool-use RL training with a tight schema |
| `step` | `StepLeanEnv` | Model emits one tactic per turn; env returns the new proof state | LeanDojo-style tactic-level interaction (local only) |
| `rlm` | `RLMEnv` (Prime) | Root model recurses (`llm_batch`) + verifies **in-sandbox** via a baked Lean+Mathlib image | recursive subgoal decomposition on hard theorems |

All modes share the same scoring path:

1. Build the Lean file from imports + theorem + model proof.
2. Reject `sorry`, `admit`, local axioms, `unsafe`, and metaprogramming before checking.
3. Run the configured checker.
4. Return `1.0` only when Lean verifies the proof.
5. On the local backend, a verified proof is additionally **axiom-audited**
   (`#print axioms`): rejected unless its axiom set ⊆ {`propext`, `Classical.choice`,
   `Quot.sound`}, catching `sorry`/`native_decide`/user-axiom smuggling the regex misses.

**Reward shaping.** `reward_mode` adds partial credit so weak models have a gradient before
they can fully prove (defeats the zero-advantage filter): `binary` (default), `shaped`
(syntax/typecheck credit), or `process` (dense first-error progress).

Anti-hacking signals remain zero-weight metrics. Reports distinguish forbidden proofs,
wrong-theorem submissions, timeouts, axiom-audit failures, and verified proofs for which
the selected backend could not run an axiom audit. Multi-turn and tool modes also retain a
structured `lean_attempt_traces` history with proof text/hash, verifier result, normalized
error kind, feedback, and forbidden reasons. A later valid proof therefore does not erase
evidence of an earlier hacked or duplicate attempt.

### External training pipeline

This environment is the final verifier-backed stage, not the owner of the SFT or
midtraining corpora. The currently planned pipeline is:

1. **Syntax SFT, outside this package.** Use `deepseek/deepseek-v4-flash` to generate a
   narrow corpus that teaches Lean 4 declaration structure, proof-body formatting, common
   tactics, and compiler-guided correction. Verify generated proofs before accepting them,
   and do not synthesize from the held-out MiniF2F test, ProofNet, or Putnam splits.
2. **Repository midtraining, outside this package.** Continue training on appropriately
   licensed Lean repositories and Mathlib-style theorem/proof files, deduplicated against
   evaluation statements. This stage should teach repository vocabulary and proof
   generation rather than environment interaction.
3. **RL and evaluation in this environment.** Start the resulting checkpoint in `tool`
   mode for constrained whole-proof checks, then use `repair`, `step`, or `rlm` when the
   model is ready for richer verifier feedback or decomposition.

This separation follows the useful part of [Lean-STaR](https://leanstar.github.io/): retain
Lean-verified proof trajectories and iterative feedback, but keep corpus construction as a
separate training pipeline. Search traces from the harness below can be exported to that
pipeline as successful proof examples, failed repair examples, or preference pairs. They
must be rechecked and filtered externally before becoming training data.

#### What Leanstral changes

Mistral has not published the promised Leanstral technical report as a paper yet. The
detailed public source is the official [Leanstral 1.5 release
report](https://mistral.ai/news/leanstral-1-5/), which describes **midtraining -> SFT ->
RL with CISPO**, rather than syntax SFT followed by midtraining. It also trains against two
different RL surfaces:

- a multiturn theorem environment where the model submits a proof, receives Lean compiler
  feedback, and retries;
- a repository code-agent environment with filesystem edits, bash, Lean LSP feedback,
  auxiliary lemmas, and long context compaction, followed by target-theorem verification.

That suggests one adjustment to our plan: if the DeepSeek-generated syntax dataset is used
before repository midtraining as a warm start, run a short verifier-filtered SFT refresh
after midtraining so proof formatting and tool behavior are not washed out. Keep all three
corpora outside this package.

This env directly covers Leanstral's first RL surface through `repair`, `tool`, and `step`.
The MaxProof-lite harness adds small population search and verified rewrites. It does **not**
replace Leanstral's second surface: realistic repository proof engineering still needs a
filesystem/LSP environment such as `opencode-lean`. That split is deliberate; this package
remains the controlled verifier and rollout-trace component.

### Recommended training mode

For a model you want to train inside this env, start with `mode="tool"`:

```bash
prime rl run configs/rl/lean_agentic_local.toml
```

`tool` is an alias for the older `agentic` name. The model-facing schema exposes only:

```text
lean_check(proof: str) -> str
```

The env injects the task imports, exact theorem statement, timeout, and rollout state into
the checker call. That keeps the action space simple for RL while preventing the model
from checking or scoring an easier theorem. The tool can be called multiple times per
rollout; the rubric rewards `1.0` if any in-budget attempt verifies, and logs `num_checks`
as a metric so tool spam can be monitored separately.

### MaxProof-lite search

`scripts/run_maxproof_lite.py` is a small, cost-bounded search harness inspired by
[MaxProof](https://ssawant.github.io/posts/MaxProof/MaxProof.html). For each theorem it:

1. generates a small population of independent proof candidates;
2. verifies every unique candidate through the same strict reward path;
3. ranks failures by Lean progress, then asks for one minimal patch and one independent
   rewrite for each selected parent;
4. selects the shortest proof that verifies the exact target theorem.

The CLI defaults to three tasks, two initial candidates, one repair round, and one parent:
at most twelve model calls when every initial proof fails. Start with one task to measure
the chosen model's token cost before increasing that budget:

```bash
uv run --project environments/lean-verifier-env python \
  environments/lean-verifier-env/scripts/run_maxproof_lite.py \
  --provider openrouter --model deepseek/deepseek-v4-flash \
  --dataset toy --max-tasks 3 --num-candidates 2 --beam-width 1 \
  --rewrite-rounds 1 --backend local \
  --lean-repl-cmd /path/repl --lean-project-dir /path/mathlib
```

The command writes a JSONL attempt archive and a Markdown report. The report includes
verified pass rate, checker calls, attempts to first solution, forbidden-proof rate,
wrong-theorem rate, timeout rate, and duplicate rate. Search does not alter RL rewards and
is never treated as proof-repair success in the scorer.

### RLM mode (recursive, in-sandbox verification)

`mode="rlm"` is the recursive-language-model path. Instead of asking one model to emit a
single proof string, the environment wraps Prime's `RLMEnv` and gives the root model a
sandboxed Python REPL. The root model can decompose the theorem into smaller Lean subgoals,
call `llm_batch()` to ask sub-model instances for those pieces, verify candidate Lean code,
and then assemble one final proof for the original theorem.

The intended loop is:

1. The root model receives the original theorem statement.
2. It proposes `have`-style subgoals or helper lemmas.
3. It dispatches those subgoals to sub-model calls through `llm_batch()`.
4. It checks returned Lean snippets with `lean_check` inside the sandbox.
5. It assembles a complete Lean file for the original theorem.
6. The env rubric gives reward only if the final assembled proof verifies.

Verification has two layers:

- **In-sandbox process feedback**: with `in_sandbox_verify=True` (default), the sandbox
  image contains Lean 4, Mathlib, and a `lean_check` CLI. The root model can run
  `lean_check` during its reasoning loop without needing Lean in Prime's host env
  container.
- **Final reward scoring**: the rubric still re-checks the final answer through this env's
  normal verifier path. For hosted Prime runs, use `scoring_backend="axle"` because the
  env container does not have local Lean installed. For local runs, scoring can use the
  local backend if you provide `LEAN_REPL_CMD` and `LEAN_PROJECT_DIR`.

This makes RLM different from the other modes:

| Mode | What the model controls | Feedback shape |
|------|--------------------------|----------------|
| `single` | One proof attempt | Final verifier reward only |
| `repair` | One proof per turn | Lean diagnostics after failed attempts |
| `tool` / `agentic` | Calls `lean_check(proof)` directly | Whole-proof verifier tool feedback |
| `step` | One tactic per turn | Live proof-state feedback |
| `rlm` | Recursive subproblem calls + final assembly | Sub-model proofs, sandbox checks, final strict reward |

Use RLM when the theorem is too hard for a direct proof-string attempt and you want the
model to explicitly decompose the work. It is heavier than `single`, `repair`, or
`agentic`, but it can expose useful process structure on MiniF2F/ProofNet/Putnam-style
tasks.

The sandbox image is baked from `sandbox/Dockerfile` as `lean-rlm-sandbox`. This needs no
Lean on Prime's host/env container. Because the rubric's final score runs in the env
container, set `scoring_backend="axle"` for hosted runs so scoring works independently of
the in-sandbox verification:

```bash
# -p prime for Prime Intellect inference, or -p openrouter (needs OPENROUTER_API_KEY)
prime eval run pradheep/lean-verifier-env -m <root_model> -p prime \
  -a '{"mode":"rlm","sub_model":"<sub_model>","scoring_backend":"axle","dataset_name":"minif2f-test"}' \
  -n 5 --hosted --allow-sandbox-access --allow-tunnel-access --follow
```

See `sandbox/README.md` for the image build/push flow and `../../RLM_SANDBOX_ROADMAP.md`
for status + remaining upgrades (scoring resilience, anti-cheat parity, perf).

### Verification backends

| Backend | What it does | Strength | Limitation |
|---------|--------------|----------|------------|
| `axle` | Calls the hosted Axiom Lean Engine / AXLE API | No local Lean setup; useful for smoke tests and low-throughput evals | Hosted API latency and concurrency limits; not ideal as the main RL backend |
| `local` | Runs Lean 4 + Mathlib through a persistent `repl` worker pool | Scales with CPU workers; no AXLE request cap; best for RL training | Requires Mathlib cache + `repl` binary; each worker needs significant RAM |

AXLE is kept deliberately as a developer-convenience backend. It is good for quick
proof-checking and debugging, but the local backend is the intended training path.

### Dataset presets

Built-in dataset names:

| Name | Source | Use | Notes |
|------|--------|-----|-------|
| `toy` | hand-written smoke split | train/eval | tiny sanity-check set |
| `lean-workbook` | cleaned Lean Workbook rows | **train** | algebra/tactic-heavy; good for early reward signal; clean (0 overlap with the benchmarks) |
| `minif2f-valid` | cleaned MiniF2F validation | train/eval | formal olympiad-style validation split |
| `minif2f-test` | cleaned MiniF2F test | **eval-only** | needs `eval_split=true`; standard MiniF2F test benchmark |
| `proofnet` | cleaned ProofNet test rows | **eval-only** | needs `eval_split=true`; standard ProofNet benchmark |
| `putnam` | cleaned Putnam rows | **eval-only** | needs `eval_split=true`; standard Putnam benchmark |

**Dataset hygiene / contamination.** `minif2f-test`, `proofnet`, and `putnam`
*are* the standard evaluation benchmarks, so they are held out for evaluation:
loading one for training raises unless you pass `eval_split=true` (env arg). Train
on `lean-workbook` (verified zero statement overlap with miniF2F/ProofNet/Putnam)
and/or `minif2f-valid`, and keep the `*-test`/`proofnet`/`putnam` presets for
evaluation:

```bash
# evaluation on a benchmark (explicit opt-in):
prime eval run pradheep/lean-verifier-env -m <model> -p prime \
  -a '{"dataset_name": "minif2f-test", "eval_split": true}'
```

You can also pass a path to a generated/cleaned JSONL file:

```bash
vf-eval lean_verifier_env \
  -a '{"dataset_name": "path/to/tasks.jsonl", "max_difficulty": "easy"}'
```

Each row uses the shared schema:

```json
{
  "id": "nat_add_zero",
  "source": "toy",
  "imports": ["Mathlib"],
  "formal_statement": "theorem nat_add_zero (n : Nat) : n + 0 = n := by",
  "proof": "simp",
  "difficulty": "toy",
  "timeout_s": 10,
  "environment": "lean-4.28.0"
}
```

### Evaluation results

The table below summarizes local-backend, single-turn evals from `outputs/evals/`.
Metric is verified pass rate (`verified == 1.0`). These are small/medium diagnostic
runs, not a leaderboard.

| Model | Dataset | Examples | Verified | Run |
|-------|---------|----------|----------|-----|
| `openai/gpt-oss-120b` | `lean-workbook` | 25 | **100.0%** | `0a96060c` |
| `openai/gpt-oss-120b` | `lean-workbook` | 29 | **93.1%** | `ab6656ff` |
| `qwen/qwen3.5-9b` | `lean-workbook` | 25 | **92.0%** | `a7785432` |
| `qwen/qwen3.5-9b` | `lean-workbook` | 29 | **89.7%** | `a22f4551` |
| `deepseek/deepseek-v4-flash` | `lean-workbook` | 30 | **86.7%** | `c80dd9fa` |
| `openai/gpt-oss-120b` | `minif2f-test` | 57 | **28.1%** | `fcd22677` |
| `deepseek/deepseek-v4-flash` | `minif2f-test` | 44 | **38.6%** | `191821e7` |
| `deepseek/deepseek-v4-flash` | `minif2f-test` | 30 | **23.3%** | `10f2a096` |
| `qwen/qwen3.5-9b` | `minif2f-test` | 30 | **20.0%** | `00954a36` |
| `openai/gpt-oss-120b` | `proofnet` | 30 | **13.3%** | `2a8318b0` |
| `deepseek/deepseek-v4-flash` | `proofnet` | 30 | **0.0%** | `9c51fe09` |
| `openai/gpt-oss-120b` | `putnam` | 30 | **0.0%** | `7756f065` |
| `deepseek/deepseek-v4-flash` | `putnam` | 23 | **0.0%** | `30d75c9c` |

Takeaways:

- The environment is already useful for measuring proof-string success on easy and
  medium algebraic Lean Workbook tasks.
- MiniF2F has a nonzero signal but is much harder.
- ProofNet and Putnam remain mostly unsolved in these single-turn runs, which makes
  them better suited for curriculum, repair, or agentic modes.

### Comparison with `opencode-lean`

`primeintellect/opencode-lean` uses `ComposableEnv` with `LeanTaskSet` and the
OpenCode harness. The model gets bash/edit tools inside a Prime sandbox and edits a
proof file in a version-matched Mathlib image. It is the better fit for realistic
coding-agent theorem proving.

This env is intentionally narrower:

- The model usually submits proof text, not arbitrary shell commands.
- `agentic` mode exposes only `lean_check(proof)`; the env injects the real theorem
  into each tool call.
- Reward is easier to audit because it goes through a single checker path.
- AXLE can be used for low-throughput smoke tests; local Lean is used for training.

Use `opencode-lean` when you want repo-like agent behavior. Use `lean-verifier-env`
when you want controlled verifier reward experiments, curriculum construction, and
RLM training signal design.

### Running modes

Single turn:

```bash
vf-eval lean_verifier_env \
  -a '{"dataset_name": "minif2f-test", "mode": "single", "checker_backend": "local"}'
```

Repair loop:

```bash
vf-eval lean_verifier_env \
  -a '{"dataset_name": "minif2f-test", "mode": "repair", "max_turns": 3, "checker_backend": "local"}'
```

Agentic tool-use:

```bash
vf-eval lean_verifier_env \
  -a '{"dataset_name": "minif2f-test", "mode": "agentic", "max_turns": 4, "checker_backend": "local"}'
```

### Training with prime-rl

Training configs live in `configs/rl/`. The local-backend configs assume a container
with Lean, Mathlib, and `repl` installed at the paths used in
`environments/lean-verifier-env/Dockerfile`.

```bash
prime rl run configs/rl/lean_agentic_local.toml
prime rl run configs/rl/lean_repair_local.toml
```

Execution choices:

| Path | When to use |
|------|-------------|
| AXLE backend | smoke tests, quick evals, hosted proof checking without local Lean |
| local backend | high-throughput RL, many rollouts, avoiding hosted checker caps |
| OpenCode / sandbox env | long-horizon file-editing theorem proving |

### Development

From the repository root:

```bash
uv run --project environments/lean-verifier-env --extra dev python -m pytest tests
uv build --project environments/lean-verifier-env
prime env push --path environments/lean-verifier-env --plain
```

In-process tiered leaderboard (pass@1 + pass@k, no `prime eval` subprocess).
`--provider prime` uses Prime Intellect inference via your `prime login` account;
`--provider openrouter` needs `OPENROUTER_API_KEY`:

```bash
python scripts/run_leaderboard.py --provider prime \
  --models openai/gpt-oss-120b,meta-llama/llama-3.3-70b-instruct \
  --datasets minif2f-test,proofnet,putnam \
  --num-examples 20 --rollouts-per-example 4 \
  --backend local --lean-repl-cmd /path/repl --lean-project-dir /path/mathlib
```

### Changelog

#### v0.10.0

- Added a MaxProof-lite generate/verify/patch/rewrite/select harness with duplicate
  suppression, strict exact-theorem selection, JSONL attempt archives, and safety/cost
  reporting.
- Added zero-weight anti-hacking telemetry and structured attempt traces without changing
  binary reward semantics.
- Documented the external syntax-SFT -> repository-midtraining -> verifier-RL pipeline;
  dataset generation and midtraining code remain outside the environment.
- Corrected RLM scoring to consume Prime's `final_answer`, use the Python REPL protocol,
  and retain strict final theorem verification.

#### v0.9.x

- Previous line: `0.9.7`.
- `v0.9.7` — **Contamination guardrail**: the eval-only benchmark presets
  (`minif2f-test`, `proofnet`, `putnam`) now require `eval_split=true` to load,
  so a training run can't silently train on a test benchmark. Train on the clean
  `lean-workbook` split (verified zero overlap with miniF2F/ProofNet/Putnam) or
  `minif2f-valid`. Presets are labelled train vs eval-only in the README.
- `v0.9.6` — **Prime Intellect inference support**: the in-process leaderboard
  (`scripts/run_leaderboard.py`) now takes `--provider prime` (alongside
  `openrouter`/`openai`), reusing verifiers' `setup_client`/`ClientConfig` so it
  picks up `prime login` credentials from `~/.prime/config.json`. Added a shipped
  `configs/endpoints.toml` registry routing models to Prime Inference, and
  documented `prime eval run -p prime`.
- `v0.9.5` — **Local Lean backend fix**: `LocalLeanChecker` now resolves the Lean
  toolchain bin dir (via `shutil.which`, falling back to `~/.elan/bin`) onto the
  subprocess PATH for both `lake env printenv` and the `repl` worker, so the
  backend works when the running venv didn't inherit elan (previously failed with
  a cryptic "empty response from Lean REPL").
- `v0.9.4` — **Hosted-scoring decoupling**: `load_environment(mode="rlm",
  scoring_backend="axle")` builds a separate scoring checker so the rubric scores via AXLE
  in Prime's Lean-less env container while the model verifies in-sandbox. Added the
  in-process leaderboard harness (`scripts/run_leaderboard.py`) with `pass@1` + `pass@k`
  (`report.pass_at_k`), replacing the flaky `prime eval` subprocess path.
- `v0.9.3` — **RLM sandbox image**: baked Lean 4 + Mathlib into a Prime-registry image
  (`sandbox/Dockerfile`, `lean_check` CLI) so `mode="rlm"` verifies **in-sandbox**
  (`in_sandbox_verify=True` default); no Lean needed on the host container. `lean_check`
  rejects `sorry`/`admit`. Validated on real Prime infra.
- `v0.9.0`–`v0.9.2` — Added `step` mode (`StepLeanEnv`, tactic-by-tactic via the repl
  proofState API), the `rlm` wrapper over Prime's `RLMEnv`, dense `process`/`shaped`
  `reward_mode`s, post-verification axiom audit + expanded forbidden-construct filters
  (`native_decide`, `ofReduceBool`, …), and the Mathlib-syntax modernizer for fair
  ProofNet/Putnam parsing. Made multi-turn env signatures robust across `verifiers`
  versions (0.1.5 ↔ 0.1.14).

#### v0.8.x

- Previous line: `0.8.3`.
- Added cleaned dataset presets for Lean Workbook, MiniF2F, ProofNet, and Putnam.
- Added local-backend eval runs and result reporting.
- Expanded RLM-oriented modes and configs.

#### v0.7.0

- Added `agentic` mode with a constrained `lean_check(proof)` tool.
- Added local Lean Dockerfile and prime-rl configs.

#### v0.6.0

- Restructured the package into `environments/lean-verifier-env/` and published a clean
  Hub source archive.

#### v0.5.0

- Made the environment Hub-ready with package metadata, README documentation, and
  reproducible build commands.
- Added the first evaluation report path for comparing model proof success under strict
  Lean verification.
- Kept prime-rl integration as a consumer of the stable environment API, not as the
  source of reward logic.

#### v0.4.0

- Added curriculum-oriented dataset plumbing and task buckets.
- Added task-generation support from Lean declarations via AXLE extraction tools.
- Restricted AXLE proof-repair and declaration-extraction APIs to dataset generation
  workflows, never final reward scoring.

#### v0.3.0

- Added the multi-turn repair environment where the model sees normalized Lean
  diagnostics and can retry.
- Preserved strict final scoring: only the model's submitted proof can receive reward.
- Added tests around retry behavior and diagnostic feedback.

#### v0.2.0

- Added Lean diagnostic normalization and monitor metrics such as syntax/typecheck
  status, proof length, timeout, and forbidden-construct flags.
- Added task filtering for unsafe proof shortcuts, including `sorry`, `admit`, local
  axioms, and unsafe bypasses.
- Kept the main reward binary: `1.0` for verified proofs, `0.0` otherwise.

#### v0.1.0

- Established the minimal single-turn Lean proof-completion environment.
- Added fixed-import theorem tasks and AXLE-backed proof checking through `AxleClient`.
- Added the initial binary reward path using strict Lean verification.
